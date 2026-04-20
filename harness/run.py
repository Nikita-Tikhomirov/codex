#!/usr/bin/env python3
"""
Local-First Harness v2
- smoke: runtime/model/task checks
- live: normalize + validate one run record and emit audit line
- ab: build paired LOCAL_FIRST/CLOUD_ONLY dataset from run records
- gate: compute acceptance gate summary

Series cache mode:
- stable fingerprint for (config + bench_set + harness code)
- one logical series per fingerprint
- duplicate valid task_id+mode runs are blocked by default
- completed series can be reused without re-running live steps
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_FALLBACK_TRIGGERS = {"none", "validation_failed", "time_budget", "defects", "high_risk"}
SERIES_VERSION = "harness_v2_series_cache_1"


def ensure_utf8_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_cmd(command: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        cp = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return cp.returncode, cp.stdout.strip(), cp.stderr.strip()
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout: {e}"


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0


def parse_bool_yn(v: str) -> bool:
    return str(v).strip().lower() in {"yes", "y", "true", "1"}


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def load_bench_tasks(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        return []
    out: list[dict[str, Any]] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        task_id = str(t.get("task_id", "")).strip()
        task_type = str(t.get("task_type", "")).strip()
        simple_bugfix = parse_bool_yn(str(t.get("simple_bugfix", "false")))
        if task_id and task_type:
            out.append(
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "simple_bugfix": simple_bugfix,
                }
            )
    return out


def bench_category_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in tasks:
        cat = str(t.get("task_type", ""))
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def canonical_json_hash(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except json.JSONDecodeError:
        canonical = path.read_text(encoding="utf-8", errors="ignore")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def choose_default_mode(task_type: str, files_touched: int, risk: str, simple_bugfix: bool, cfg: dict[str, Any]) -> str:
    default = cfg["routing"]["default"]
    mode = default.get(task_type, "CLOUD_ONLY")
    if mode == "CONDITIONAL_SIMPLE_LOCAL":
        rule = cfg["routing"]["bugfix_simple"]
        if simple_bugfix and files_touched <= int(rule["max_files_touched"]) and risk in set(rule["allowed_risk"]):
            return rule["mode"]
        return rule["fallback_mode"]
    return mode


def nontrivial(task_type: str, files_touched: int) -> bool:
    return task_type in {"ui_logic", "refactor", "bugfix"} or files_touched > 1


def validate_record(row: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, list[str]]:
    issues: list[str] = []

    if re.search(r"healthcheck", str(row.get("reason", "")), flags=re.IGNORECASE):
        issues.append("reason_looks_like_healthcheck")

    max_cloud_calls = int(cfg["cloud_budget"]["max_cloud_calls_per_task"])
    if safe_float(row.get("cloud_calls")) > max_cloud_calls:
        issues.append("cloud_calls_exceeds_budget")

    if row.get("mode") == "LOCAL_FIRST":
        if nontrivial(str(row.get("task_type")), int(row.get("files_touched", 0))):
            if row.get("local_passes") in (None, ""):
                issues.append("missing_local_passes_for_nontrivial")
            elif safe_float(row.get("local_passes")) < 1:
                issues.append("invalid_local_passes_for_nontrivial")
            if row.get("retrieval_used") in (None, ""):
                issues.append("missing_retrieval_used_for_nontrivial")

        if not row.get("local_model_chain"):
            issues.append("missing_local_model_chain")

    first_draft = safe_float(row.get("first_draft_sec"))
    ready = safe_float(row.get("ready_sec"))
    if first_draft > ready and ready > 0:
        issues.append("first_draft_greater_than_ready")

    if nontrivial(str(row.get("task_type")), int(row.get("files_touched", 0))) and abs(first_draft - ready) < 1e-9:
        notes = str(row.get("notes", ""))
        if "no-iteration" not in notes.lower():
            issues.append("equal_draft_and_ready_without_explanation")

    if parse_bool_yn(str(row.get("cloud_fallback", "no"))):
        trigger = str(row.get("fallback_trigger", "none"))
        if trigger == "none":
            issues.append("fallback_trigger_missing")

    trigger = str(row.get("fallback_trigger", "none"))
    if trigger not in ALLOWED_FALLBACK_TRIGGERS:
        issues.append("fallback_trigger_not_allowed")

    return len(issues) == 0, issues


def audit_line(row: dict[str, Any]) -> str:
    return (
        "LLM_LAYER: "
        f"mode={row.get('mode')}, "
        f"local_model={row.get('local_model_chain', 'none')}, "
        f"first_draft_sec={row.get('first_draft_sec')}, "
        f"ready_sec={row.get('ready_sec')}, "
        f"cloud_fallback={row.get('cloud_fallback')}, "
        f"reason={row.get('reason')}, "
        f"cloud_calls={row.get('cloud_calls')}, "
        f"fallback_trigger={row.get('fallback_trigger')}"
    )


@dataclass
class SeriesPaths:
    series_id: str
    fingerprint: str
    series_root: Path
    series_dir: Path
    series_runs: Path
    series_ab: Path
    series_gate: Path
    series_meta: Path
    series_index: Path
    latest_final: Path
    config_path: Path
    bench_set_path: Path
    harness_path: Path


def resolve_series(args: argparse.Namespace, config_path: Path) -> SeriesPaths:
    bench_set_path = Path(args.bench_set).resolve()
    harness_path = Path(__file__).resolve()
    cfg_hash = canonical_json_hash(config_path)
    bench_hash = canonical_json_hash(bench_set_path)
    harness_hash = file_hash(harness_path)
    fingerprint_seed = f"{SERIES_VERSION}|{cfg_hash}|{bench_hash}|{harness_hash}"
    fingerprint = hashlib.sha256(fingerprint_seed.encode("utf-8")).hexdigest()

    series_id = args.series_id.strip() if args.series_id else fingerprint[:16]
    series_root = Path(args.series_root)
    series_dir = series_root / series_id

    return SeriesPaths(
        series_id=series_id,
        fingerprint=fingerprint,
        series_root=series_root,
        series_dir=series_dir,
        series_runs=series_dir / "runs.jsonl",
        series_ab=series_dir / "ab_results.json",
        series_gate=series_dir / "gate_summary.json",
        series_meta=series_dir / "meta.json",
        series_index=series_root / "series_index.json",
        latest_final=series_root / "latest_final.json",
        config_path=config_path,
        bench_set_path=bench_set_path,
        harness_path=harness_path,
    )


def load_meta(paths: SeriesPaths) -> dict[str, Any]:
    meta = read_json(paths.series_meta, default={})
    if not meta:
        meta = {
            "series_id": paths.series_id,
            "fingerprint": paths.fingerprint,
            "status": "running",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "config_path": str(paths.config_path),
            "bench_set_path": str(paths.bench_set_path),
            "harness_path": str(paths.harness_path),
        }
        write_json(paths.series_meta, meta)
    return meta


def update_meta(paths: SeriesPaths, meta: dict[str, Any], status: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = dict(meta)
    if status:
        meta["status"] = status
    if extra:
        meta.update(extra)
    meta["updated_at"] = now_iso()
    write_json(paths.series_meta, meta)

    index = read_json(paths.series_index, default={"fingerprints": {}, "series": {}})
    if "fingerprints" not in index:
        index["fingerprints"] = {}
    if "series" not in index:
        index["series"] = {}

    index["fingerprints"][paths.fingerprint] = {
        "series_id": paths.series_id,
        "status": meta.get("status", "running"),
        "updated_at": meta["updated_at"],
    }
    index["series"][paths.series_id] = {
        "fingerprint": paths.fingerprint,
        "status": meta.get("status", "running"),
        "updated_at": meta["updated_at"],
    }
    write_json(paths.series_index, index)
    return meta


def write_latest_final(paths: SeriesPaths, gate_payload: dict[str, Any]) -> None:
    payload = {
        "series_id": paths.series_id,
        "fingerprint": paths.fingerprint,
        "updated_at": now_iso(),
        "gate_verdict": gate_payload.get("gate", {}).get("verdict", "unknown"),
        "gate_interpretable": gate_payload.get("gate", {}).get("interpretable", False),
        "artifacts": {
            "runs": str(paths.series_runs.resolve()),
            "ab": str(paths.series_ab.resolve()),
            "gate": str(paths.series_gate.resolve()),
            "meta": str(paths.series_meta.resolve()),
        },
    }
    write_json(paths.latest_final, payload)


def maybe_reuse_final(paths: SeriesPaths, meta: dict[str, Any], target_kind: str) -> tuple[bool, dict[str, Any] | None]:
    if meta.get("status") != "final":
        return False, None
    if target_kind == "ab" and paths.series_ab.exists():
        payload = read_json(paths.series_ab, default={})
        if payload:
            payload["reused_existing_series"] = True
            payload["series_status"] = "final"
            return True, payload
    if target_kind == "gate" and paths.series_gate.exists():
        payload = read_json(paths.series_gate, default={})
        if payload:
            payload["reused_existing_series"] = True
            payload["series_status"] = "final"
            return True, payload
    return False, None


def pick_rows_for_series(paths: SeriesPaths, fallback_input: Path) -> list[dict[str, Any]]:
    series_rows = load_jsonl(paths.series_runs)
    if series_rows:
        return series_rows

    rows = load_jsonl(fallback_input)
    filtered: list[dict[str, Any]] = []
    for r in rows:
        if r.get("series_id") == paths.series_id or r.get("fingerprint") == paths.fingerprint:
            filtered.append(r)
    return filtered


def cmd_live(args: argparse.Namespace, cfg: dict[str, Any], config_path: Path) -> int:
    mode = args.mode
    if mode == "AUTO":
        mode = choose_default_mode(args.task_type, args.files_touched, args.risk, args.simple_bugfix, cfg)

    paths = resolve_series(args, config_path)
    meta = load_meta(paths)

    if meta.get("status") == "final" and not args.force:
        payload = {
            "error": "series_finalized",
            "series_id": paths.series_id,
            "fingerprint": paths.fingerprint,
            "message": "Series already finalized. Use --force to append new runs.",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 5

    existing_rows = load_jsonl(paths.series_runs)
    existing_valid = None
    for r in reversed(existing_rows):
        if (
            str(r.get("task_id", "")) == args.task_id
            and str(r.get("mode", "")) == mode
            and not bool(r.get("invalid_audit", False))
        ):
            existing_valid = r
            break

    if existing_valid and not args.force:
        payload = {
            "error": "duplicate_pair_blocked",
            "series_id": paths.series_id,
            "fingerprint": paths.fingerprint,
            "task_id": args.task_id,
            "mode": mode,
            "message": "Valid run for this task_id+mode already exists in this series. Use --force to override.",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 4

    row = {
        "timestamp": now_iso(),
        "series_id": paths.series_id,
        "fingerprint": paths.fingerprint,
        "task_id": args.task_id,
        "task_type": args.task_type,
        "files_touched": args.files_touched,
        "risk": args.risk,
        "simple_bugfix": args.simple_bugfix,
        "mode": mode,
        "local_model_chain": args.local_model_chain,
        "local_passes": args.local_passes,
        "retrieval_used": args.retrieval_used,
        "retrieval_hit_score": args.retrieval_hit_score,
        "first_draft_sec": args.first_draft_sec,
        "ready_sec": args.ready_sec,
        "defects_found": args.defects_found,
        "success": args.success,
        "cloud_calls": args.cloud_calls,
        "cloud_fallback": args.cloud_fallback,
        "fallback_trigger": args.fallback_trigger,
        "tests_passed": args.tests_passed,
        "notes": args.notes,
        "reason": args.reason,
    }

    ok, issues = validate_record(row, cfg)
    row["invalid_audit"] = not ok
    row["invalid_reasons"] = issues

    append_jsonl(paths.series_runs, row)
    output_path = Path(args.output)
    if output_path.resolve() != paths.series_runs.resolve():
        append_jsonl(output_path, row)

    update_meta(paths, meta, status="running")

    print(audit_line(row))
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


def cmd_ab(args: argparse.Namespace, cfg: dict[str, Any], config_path: Path) -> int:
    bench_cfg = cfg.get("bench", {})
    paths = resolve_series(args, config_path)
    meta = load_meta(paths)

    if args.reuse_series:
        reused, payload = maybe_reuse_final(paths, meta, "ab")
        if reused and payload is not None:
            write_json(Path(args.output), payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

    bench_tasks = load_bench_tasks(paths.bench_set_path)
    bench_map = {str(t["task_id"]): t for t in bench_tasks}
    bench_ids = set(bench_map.keys())
    config_required_counts = bench_cfg.get("required_counts", {})
    min_tasks = int(bench_cfg.get("min_tasks", len(bench_ids)))

    rows = pick_rows_for_series(paths, Path(args.input))
    by_task: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r.get("invalid_audit"):
            continue
        task_id = str(r.get("task_id", ""))
        mode = str(r.get("mode", ""))
        if not task_id or mode not in {"LOCAL_FIRST", "CLOUD_ONLY"}:
            continue
        by_task.setdefault(task_id, {})[mode] = r

    pairs = []
    iter_ids = sorted(bench_ids) if bench_ids else sorted(by_task.keys())
    for task_id in iter_ids:
        m = by_task.get(task_id, {})
        if "LOCAL_FIRST" in m and "CLOUD_ONLY" in m:
            pairs.append(
                {
                    "task_id": task_id,
                    "task_type": bench_map.get(task_id, {}).get("task_type")
                    or m["LOCAL_FIRST"].get("task_type")
                    or m["CLOUD_ONLY"].get("task_type"),
                    "local": m["LOCAL_FIRST"],
                    "cloud": m["CLOUD_ONLY"],
                }
            )

    missing_local = []
    missing_cloud = []
    for task_id in iter_ids:
        modes = by_task.get(task_id, {})
        if "LOCAL_FIRST" not in modes:
            missing_local.append(task_id)
        if "CLOUD_ONLY" not in modes:
            missing_cloud.append(task_id)

    unexpected_task_ids = sorted(set(by_task.keys()) - bench_ids) if bench_ids else []
    pair_category_counts = bench_category_counts([{"task_type": p.get("task_type", "unknown")} for p in pairs])
    expected_category_counts = bench_category_counts(bench_tasks) if bench_tasks else {}

    category_missing = {}
    for cat, need in expected_category_counts.items():
        have = pair_category_counts.get(cat, 0)
        category_missing[cat] = max(int(need) - int(have), 0)

    required_counts = expected_category_counts if expected_category_counts else config_required_counts
    required_category_ok = True
    for cat, need in required_counts.items():
        if pair_category_counts.get(cat, 0) < int(need):
            required_category_ok = False
            break

    pair_count = len(pairs)
    expected_total_tasks = len(iter_ids)
    pair_count_target = expected_total_tasks if expected_total_tasks > 0 else min_tasks
    pair_count_ok = pair_count >= pair_count_target
    complete = pair_count_ok and required_category_ok and not missing_local and not missing_cloud

    payload = {
        "generated_at": now_iso(),
        "series_id": paths.series_id,
        "fingerprint": paths.fingerprint,
        "series_status": meta.get("status", "running"),
        "reused_existing_series": False,
        "pairs": pairs,
        "pair_count": pair_count,
        "pair_count_target": pair_count_target,
        "expected_total_tasks": expected_total_tasks,
        "missing_local_modes": missing_local,
        "missing_cloud_modes": missing_cloud,
        "unexpected_task_ids": unexpected_task_ids,
        "expected_category_counts": expected_category_counts,
        "pair_category_counts": pair_category_counts,
        "category_missing": category_missing,
        "required_category_counts": required_counts,
        "complete_pair_coverage": complete,
    }

    write_json(paths.series_ab, payload)
    write_json(Path(args.output), payload)

    meta = update_meta(paths, meta, status="ab_ready" if complete else "running")
    payload["series_status"] = meta.get("status", "running")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.require_complete and not complete:
        return 3
    return 0


def summarize_mode(rows: list[dict[str, Any]]) -> dict[str, Any]:
    drafts = [safe_float(r.get("first_draft_sec")) for r in rows]
    ready = [safe_float(r.get("ready_sec")) for r in rows]
    defects = [safe_float(r.get("defects_found")) for r in rows]
    cloud_calls = [safe_float(r.get("cloud_calls")) for r in rows]
    success = [1.0 if parse_bool_yn(str(r.get("success", "no"))) else 0.0 for r in rows]
    return {
        "count": len(rows),
        "first_draft_avg": mean(drafts),
        "ready_avg": mean(ready),
        "defects_avg": mean(defects),
        "cloud_calls_avg": mean(cloud_calls),
        "success_rate": mean(success),
    }


def cmd_gate(args: argparse.Namespace, cfg: dict[str, Any], config_path: Path) -> int:
    paths = resolve_series(args, config_path)
    meta = load_meta(paths)

    if args.reuse_series:
        reused, payload = maybe_reuse_final(paths, meta, "gate")
        if reused and payload is not None:
            write_json(Path(args.output), payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

    input_path = Path(args.input)
    if not input_path.exists() and paths.series_ab.exists():
        input_path = paths.series_ab

    data = read_json(input_path, default={})
    pairs = data.get("pairs", [])

    local_rows = [p["local"] for p in pairs]
    cloud_rows = [p["cloud"] for p in pairs]

    local_summary = summarize_mode(local_rows)
    cloud_summary = summarize_mode(cloud_rows)

    acceptance = cfg["acceptance"]
    bench_cfg = cfg.get("bench", {})
    complete_pair_coverage = bool(data.get("complete_pair_coverage", False))
    pair_count = int(data.get("pair_count", len(pairs)))
    expected_pair_count = int(data.get("pair_count_target", int(bench_cfg.get("min_tasks", 0))))

    gate: dict[str, Any]
    if not complete_pair_coverage:
        gate = {
            "interpretable": False,
            "passed": False,
            "verdict": "insufficient_data",
            "reason": "pair_coverage_incomplete",
            "pair_count": pair_count,
            "pair_count_target": expected_pair_count,
            "cloud_calls_reduction": 0.0,
            "cloud_calls_reduction_ok": False,
            "success_drop_pp": 0.0,
            "success_ok": False,
            "defects_delta": 0.0,
            "defects_ok": False,
            "local_accept": 0.0,
            "local_accept_ok": False,
        }
    else:
        cloud_calls_reduction = 0.0
        if cloud_summary["cloud_calls_avg"] > 0:
            cloud_calls_reduction = (cloud_summary["cloud_calls_avg"] - local_summary["cloud_calls_avg"]) / cloud_summary["cloud_calls_avg"]

        success_drop_pp = (cloud_summary["success_rate"] - local_summary["success_rate"]) * 100
        defects_delta = local_summary["defects_avg"] - cloud_summary["defects_avg"]

        local_accept_candidates = [
            r
            for r in local_rows
            if r.get("task_type") == "layout"
            or (r.get("task_type") == "bugfix" and parse_bool_yn(str(r.get("simple_bugfix", "no"))))
        ]
        if local_accept_candidates:
            local_accept = mean(
                [
                    1.0
                    if parse_bool_yn(str(r.get("success", "no"))) and safe_float(r.get("cloud_calls")) == 0
                    else 0.0
                    for r in local_accept_candidates
                ]
            )
        else:
            local_accept = 0.0

        gate = {
            "interpretable": True,
            "cloud_calls_reduction": round(cloud_calls_reduction, 4),
            "cloud_calls_reduction_ok": cloud_calls_reduction >= float(acceptance["cloud_calls_reduction_min"]),
            "success_drop_pp": round(success_drop_pp, 4),
            "success_ok": success_drop_pp <= float(acceptance["success_rate_drop_max_pp"]),
            "defects_delta": round(defects_delta, 4),
            "defects_ok": defects_delta <= float(acceptance["defects_delta_max"]),
            "local_accept": round(local_accept, 4),
            "local_accept_ok": local_accept >= float(acceptance["local_accept_layout_simple_bugfix_min"]),
            "pair_count": pair_count,
            "pair_count_target": expected_pair_count,
        }
        gate["passed"] = all(
            [
                gate["cloud_calls_reduction_ok"],
                gate["success_ok"],
                gate["defects_ok"],
                gate["local_accept_ok"],
            ]
        )
        gate["verdict"] = "pass" if gate["passed"] else "fail"

    stability_path = Path(args.stability)
    stability = json.loads(stability_path.read_text(encoding="utf-8-sig")) if stability_path.exists() else {}

    out_payload = {
        "generated_at": now_iso(),
        "series_id": paths.series_id,
        "fingerprint": paths.fingerprint,
        "series_status": meta.get("status", "running"),
        "reused_existing_series": False,
        "local_summary": local_summary,
        "cloud_summary": cloud_summary,
        "gate": gate,
        "ab_meta": {
            "pair_count": pair_count,
            "pair_count_target": expected_pair_count,
            "complete_pair_coverage": complete_pair_coverage,
            "missing_local_modes": data.get("missing_local_modes", []),
            "missing_cloud_modes": data.get("missing_cloud_modes", []),
            "expected_category_counts": data.get("expected_category_counts", {}),
            "pair_category_counts": data.get("pair_category_counts", {}),
            "category_missing": data.get("category_missing", {}),
        },
        "stability": stability,
    }

    write_json(paths.series_gate, out_payload)
    write_json(Path(args.output), out_payload)

    finalizable = bool(complete_pair_coverage) and bool(gate.get("interpretable", False))
    meta = update_meta(paths, meta, status="final" if finalizable else "running")
    out_payload["series_status"] = meta.get("status", "running")

    if finalizable:
        write_latest_final(paths, out_payload)

    print(json.dumps(out_payload, ensure_ascii=False, indent=2))
    return 0


def parse_task_status(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out


def resolve_watchdog_log(log_value: str, root: str) -> Path:
    raw = Path(log_value)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(Path(root) / raw)
        candidates.append(Path("C:/Users/user/.codex") / raw)
        candidates.append(Path("C:/Users/user/Desktop/codex") / raw)

    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def cmd_smoke(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    smoke_cfg = cfg["smoke"]
    report: dict[str, Any] = {"generated_at": now_iso(), "checks": {}}

    rc, out, err = run_cmd(["ollama", "list"], timeout=120)
    report["checks"]["ollama_list"] = {"ok": rc == 0, "stderr": err, "stdout_preview": out[:1200]}

    ready_checks = []
    for item in smoke_cfg["ready_models"]:
        model = item["model"]
        probe = item["probe"]
        rc, out, err = run_cmd(["ollama", "run", model, probe], timeout=180)
        ready_checks.append({"model": model, "ok": rc == 0, "stdout": out.strip(), "stderr": err.strip()})
    report["checks"]["readiness"] = ready_checks

    for label, task_name in (("serve_task", smoke_cfg["serve_task"]), ("watchdog_task", smoke_cfg["watchdog_task"])):
        rc, out, err = run_cmd(["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"], timeout=60)
        task = parse_task_status(out)
        report["checks"][label] = {
            "ok": rc == 0,
            "status": task.get("Status", ""),
            "last_result": task.get("Last Result", ""),
            "stderr": err,
        }

    log_path = resolve_watchdog_log(str(smoke_cfg["watchdog_log"]), args.root)
    tail = []
    if log_path.exists():
        tail = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-20:]
    report["checks"]["watchdog_log_tail"] = tail

    drive = args.drive.upper()
    if os.name == "nt":
        rc, out, err = run_cmd(
            ["powershell", "-NoProfile", "-Command", f"$d=Get-PSDrive -Name {drive}; Write-Output $d.Free"], timeout=30
        )
        free_gb = None
        if rc == 0 and out.strip().isdigit():
            free_gb = round(int(out.strip()) / (1024**3), 2)
        report["checks"]["disk_free"] = {
            "ok": free_gb is not None and free_gb >= float(cfg["disk"]["min_free_gb"]),
            "free_gb": free_gb,
            "required_min_gb": float(cfg["disk"]["min_free_gb"]),
            "stderr": err,
        }

    out_file = Path(args.output)
    write_json(out_file, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local-First Harness v2")
    p.add_argument("--config", default="harness/config.yaml")

    sub = p.add_subparsers(dest="cmd", required=True)

    s_smoke = sub.add_parser("smoke")
    s_smoke.add_argument("--output", default="harness/smoke_report.json")
    s_smoke.add_argument("--root", default=".")
    s_smoke.add_argument("--drive", default="D")

    s_live = sub.add_parser("live")
    s_live.add_argument("--task-id", required=True)
    s_live.add_argument("--task-type", choices=["layout", "ui_logic", "bugfix", "refactor", "mixed"], required=True)
    s_live.add_argument("--mode", choices=["AUTO", "LOCAL_FIRST", "CLOUD_ONLY"], default="AUTO")
    s_live.add_argument("--files-touched", type=int, default=1)
    s_live.add_argument("--risk", choices=["low", "medium", "high"], default="low")
    s_live.add_argument("--simple-bugfix", action="store_true")
    s_live.add_argument("--local-model-chain", default="")
    s_live.add_argument("--local-passes", type=int, default=0)
    s_live.add_argument("--retrieval-used", choices=["yes", "no"], default="no")
    s_live.add_argument("--retrieval-hit-score", default="na")
    s_live.add_argument("--first-draft-sec", type=float, required=True)
    s_live.add_argument("--ready-sec", type=float, required=True)
    s_live.add_argument("--defects-found", type=int, default=0)
    s_live.add_argument("--success", choices=["yes", "no"], required=True)
    s_live.add_argument("--cloud-calls", type=int, default=0)
    s_live.add_argument("--cloud-fallback", choices=["yes", "no"], default="no")
    s_live.add_argument("--fallback-trigger", default="none")
    s_live.add_argument("--tests-passed", choices=["yes", "no", "na"], default="na")
    s_live.add_argument("--reason", required=True)
    s_live.add_argument("--notes", default="")
    s_live.add_argument("--output", default="harness/runs.jsonl")
    s_live.add_argument("--bench-set", default="harness/bench_set.json")
    s_live.add_argument("--series-root", default="harness/series")
    s_live.add_argument("--series-id", default="")
    s_live.add_argument("--force", action="store_true")

    s_ab = sub.add_parser("ab")
    s_ab.add_argument("--input", default="harness/runs.jsonl")
    s_ab.add_argument("--output", default="harness/ab_results.json")
    s_ab.add_argument("--bench-set", default="harness/bench_set.json")
    s_ab.add_argument("--series-root", default="harness/series")
    s_ab.add_argument("--series-id", default="")
    s_ab.add_argument("--require-complete", action="store_true")
    s_ab.add_argument("--reuse-series", dest="reuse_series", action="store_true", default=True)
    s_ab.add_argument("--no-reuse-series", dest="reuse_series", action="store_false")

    s_gate = sub.add_parser("gate")
    s_gate.add_argument("--input", default="harness/ab_results.json")
    s_gate.add_argument("--stability", default="harness/stability.json")
    s_gate.add_argument("--output", default="harness/gate_summary.json")
    s_gate.add_argument("--bench-set", default="harness/bench_set.json")
    s_gate.add_argument("--series-root", default="harness/series")
    s_gate.add_argument("--series-id", default="")
    s_gate.add_argument("--reuse-series", dest="reuse_series", action="store_true", default=True)
    s_gate.add_argument("--no-reuse-series", dest="reuse_series", action="store_false")

    return p


def main() -> int:
    ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    cfg = load_config(config_path)

    if args.cmd == "smoke":
        return cmd_smoke(args, cfg)
    if args.cmd == "live":
        return cmd_live(args, cfg, config_path)
    if args.cmd == "ab":
        return cmd_ab(args, cfg, config_path)
    if args.cmd == "gate":
        return cmd_gate(args, cfg, config_path)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
