#!/usr/bin/env python3
"""
Minimal local RAG utility powered by Ollama embeddings.
- index: scans project files, chunks them, stores local JSON index
- query: retrieves top-k chunks by cosine similarity
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import urllib.request
from dataclasses import dataclass, asdict
from typing import Iterable

ALLOWED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".html", ".css", ".md", ".json", ".yml", ".yaml", ".sql"
}

DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "dist", "build", "venv", ".venv", "__pycache__", ".local_rag"
}


@dataclass
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str
    embedding: list[float]


def ollama_embed(model: str, text: str, host: str) -> list[float]:
    payload = json.dumps({"model": model, "input": text}).encode("utf-8")
    req = urllib.request.Request(
        url=f"{host.rstrip('/')}/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    emb = data.get("embeddings")
    if not emb or not emb[0]:
        raise RuntimeError("Embedding response is empty")
    return emb[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def iter_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]
        for name in files:
            p = pathlib.Path(base) / name
            if p.suffix.lower() in ALLOWED_EXTENSIONS:
                yield p


def chunk_lines(lines: list[str], chunk_size: int, overlap: int) -> Iterable[tuple[int, int, str]]:
    i = 0
    n = len(lines)
    while i < n:
        j = min(n, i + chunk_size)
        text = "".join(lines[i:j]).strip()
        if text:
            yield i + 1, j, text
        if j == n:
            break
        i = max(i + 1, j - overlap)


def build_index(root: pathlib.Path, out_file: pathlib.Path, model: str, host: str, chunk_size: int, overlap: int) -> None:
    chunks: list[Chunk] = []
    for path in iter_files(root):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = content.splitlines(keepends=True)
        for start, end, text in chunk_lines(lines, chunk_size, overlap):
            embedding = ollama_embed(model=model, text=text, host=host)
            chunks.append(Chunk(path=str(path), start_line=start, end_line=end, text=text, embedding=embedding))

    out_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "embedding_model": model,
        "root": str(root),
        "count": len(chunks),
        "chunks": [asdict(c) for c in chunks],
    }
    out_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"Indexed {len(chunks)} chunks -> {out_file}")


def query_index(index_file: pathlib.Path, query: str, host: str, top_k: int) -> None:
    data = json.loads(index_file.read_text(encoding="utf-8"))
    chunks = data.get("chunks", [])
    model = data["embedding_model"]
    q_emb = ollama_embed(model=model, text=query, host=host)

    scored = []
    for c in chunks:
        score = cosine_similarity(q_emb, c["embedding"])
        scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    for score, c in scored[:top_k]:
        print(f"\n[{score:.4f}] {c['path']}:{c['start_line']}-{c['end_line']}")
        print(c["text"][:600])


def main() -> None:
    # Ensure Windows console can print UTF-8 snippets from project files.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Local RAG helper")
    parser.add_argument("command", choices=["index", "query"])
    parser.add_argument("--root", default=".")
    parser.add_argument("--index", default=".local_rag/index.json")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default="bge-m3")
    parser.add_argument("--chunk-size", type=int, default=120)
    parser.add_argument("--overlap", type=int, default=24)
    parser.add_argument("--query", default="")
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()

    root = pathlib.Path(args.root).resolve()
    index = pathlib.Path(args.index).resolve()

    if args.command == "index":
        build_index(root, index, args.embedding_model, args.host, args.chunk_size, args.overlap)
    else:
        if not args.query.strip():
            raise SystemExit("--query is required for query command")
        query_index(index, args.query, args.host, args.top_k)


if __name__ == "__main__":
    main()
