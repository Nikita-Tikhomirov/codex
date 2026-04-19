# LOCAL_LLM_ROUTER

Default profile: Balanced Local-First (RTX 3060 Ti 8GB)

## Model roles
- qwen2.5-coder:7b -> default code generation (1-3 files).
- deepseek-r1:8b -> reasoning fallback for complex local tasks.
- qwen3:8b -> text/TZ/explanations.
- qwen2.5-coder:14b -> harder code pass when 7b fails.
- bge-m3 -> embeddings for local RAG retrieval.

## Routing algorithm (decision-complete)
1) Always start local draft.
2) If task is complex (>=4 files, architecture/security risk, repeated failed attempts), run second local pass with deepseek-r1:8b or qwen2.5-coder:14b.
3) If acceptance criteria still fail after two local passes, allow cloud fallback.
4) Always report:
   LLM_LAYER: mode=<LOCAL_FIRST|CLOUD_ONLY>, local_model=<model|none>, first_draft_sec=<n>, ready_sec=<n>, cloud_fallback=<yes|no>, reason=<text>

## Runtime constraints for 8GB VRAM
- OLLAMA_KEEP_ALIVE=30m
- OLLAMA_MAX_LOADED_MODELS=2
- OLLAMA_NUM_PARALLEL=1
- Prefer a single heavy model loaded at once.

## RAG usage
- Build index:
  python tools/local_rag.py index --root . --embedding-model bge-m3
- Query index:
  python tools/local_rag.py query --index .local_rag/index.json --query "your question" --top-k 6

## Disk policy
- Target: keep 20GB+ free on D:
- Pinned models: qwen2.5-coder:7b, qwen2.5-coder:14b, qwen3:8b, deepseek-r1:8b, bge-m3
- Optional cleanup:
  powershell -ExecutionPolicy Bypass -File ops/model_lru_cleanup.ps1
