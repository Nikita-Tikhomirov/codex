# LOCAL_LLM_ROUTER

Default profile: Cost-First Hybrid (RTX 3060 Ti 8GB)

## Model roles (production)
- qwen2.5-coder:7b -> fast local code draft (q4 profile, up to 30s).
- qwen2.5-coder:7b-instruct-q6_K -> strong local code pass after validation fail.
- deepseek-r1:8b -> local reviewer pass (max 8 actionable fixes, no long chain-of-thought).
- qwen3:8b -> text/TZ/explanations.
- bge-m3 -> embeddings for local RAG retrieval.
- qwen2.5-coder:14b -> cold standby (not part of default routing).

## Routing algorithm (decision-complete)
1) Always start with local fast pass (`qwen2.5-coder:7b`, budget 30s).
2) If acceptance/checks fail, run second local pass (`qwen2.5-coder:7b-instruct-q6_K`).
3) Run reviewer pass (`deepseek-r1:8b`) and apply up to 8 concrete fixes.
4) Cloud fallback allowed only if one of these is true:
   - two local passes still fail acceptance;
   - `ready_sec_local > 120`;
   - `defects_found >= 2` after local validation;
   - high-risk task (architecture/security/migration/6+ files).
5) Cloud budget: `max_cloud_calls_per_task = 1`.
   - Second cloud call only when tests are red after first cloud call.
6) Cloud prompt payload must be minimal:
   - `diff + top-k RAG (k=6) + error/trace`.
   - Never send full project dump by default.

## RAG policy
- For non-trivial tasks over existing code, retrieval is mandatory before local generation.
- If retrieval is unavailable for existing-code tasks, skip local pass and use fallback path.
- Default retrieval: `top-k=6`.
- Build/rebuild index:
  - `python tools/local_rag.py index --root . --embedding-model bge-m3`
- Query:
  - `python tools/local_rag.py query --index .local_rag/index.json --query "your question" --top-k 6`

## Runtime constraints for 8GB VRAM
- OLLAMA_KEEP_ALIVE=30m
- OLLAMA_MAX_LOADED_MODELS=2
- OLLAMA_NUM_PARALLEL=1
- OLLAMA_CONTEXT_LENGTH=4096
- Keep one generative model loaded at a time (+ embeddings on demand).
- Reviewer pass target context: `num_ctx=3072`.

## Disk policy
- Target free space on D:: keep >=20GB.
- Pinned models:
  - qwen2.5-coder:7b
  - qwen2.5-coder:7b-instruct-q6_K
  - qwen3:8b
  - deepseek-r1:8b
  - bge-m3
- Cold-standby (optional keep/remove):
  - qwen2.5-coder:14b
- Optional cleanup:
  - `powershell -ExecutionPolicy Bypass -File ops/model_lru_cleanup.ps1`

## Required audit line in final task responses
`LLM_LAYER: mode=<LOCAL_FIRST|CLOUD_ONLY>, local_model=<model|none>, first_draft_sec=<n>, ready_sec=<n>, cloud_fallback=<yes|no>, reason=<text>, cloud_calls=<n>, fallback_trigger=<none|validation_failed|time_budget|defects|high_risk>`

## Current default task-type routing (temporary rollback profile)
- layout -> LOCAL_FIRST
- ui_logic -> CLOUD_ONLY
- refactor -> CLOUD_ONLY
- bugfix -> LOCAL_FIRST only for simple fixes (1-2 files, low risk); otherwise CLOUD_ONLY
