# LOCAL_LLM_BENCHMARK

Цель: измерять, насколько Cost-First Hybrid снижает обращения к облаку без заметной потери качества.

## Режимы сравнения

- `LOCAL_FIRST`: локальные проходы по роутингу (fast -> strong -> reviewer), затем cloud fallback только по триггерам.
- `CLOUD_ONLY`: выполнение задачи облаком без локальной генерации.

## Метрики (обязательные)

- `task_id`: короткий ID задачи.
- `task_type`: `layout`, `ui_logic`, `refactor`, `bugfix`, `mixed`.
- `files_touched`: количество измененных файлов.
- `loc_changed`: суммарно добавлено/удалено строк.
- `mode`: `LOCAL_FIRST` или `CLOUD_ONLY`.
- `first_draft_sec`: время до первого рабочего черновика.
- `ready_sec`: время до финального результата.
- `local_passes`: количество локальных проходов (0..3).
- `cloud_calls`: число вызовов облака (target: <=1).
- `cloud_fallback`: `yes/no`.
- `fallback_trigger`: `none|validation_failed|time_budget|defects|high_risk`.
- `retrieval_used`: `yes/no`.
- `retrieval_hit_score`: средний score top-k (0..1, `na` если retrieval=no).
- `rework_rounds`: количество доработок после первого черновика.
- `tests_passed`: `yes/no/na`.
- `defects_found`: количество явных дефектов после проверки.
- `success`: `yes/no`.
- `notes`: что ускорило или замедлило.

## Стабильность (обязательные сутки/серия)

- `watchdog_restarts_day`: число рестартов watchdog за сутки.
- `crash_count`: число падений Ollama.
- `successful_runs_without_manual`: доля успешных прогонов без ручного вмешательства, %.

## Acceptance gate (Cost-First Hybrid)

Профиль считаем успешным, если одновременно:

1. `cloud_calls` снижены минимум на 40%.
2. `success_rate` не хуже более чем на 5 п.п. относительно `CLOUD_ONLY`.
3. `defects_found` не выше более чем на +0.3 в среднем.
4. Для `layout` и простых `bugfix`: `LOCAL_ACCEPT >= 80%` без облака.

## Шаблон записи

| task_id | task_type | files_touched | loc_changed | mode | first_draft_sec | ready_sec | local_passes | cloud_calls | cloud_fallback | fallback_trigger | retrieval_used | retrieval_hit_score | rework_rounds | tests_passed | defects_found | success | notes |
|---|---|---:|---:|---|---:|---:|---:|---:|---|---|---|---:|---:|---|---:|---|---|
| EXAMPLE-001 | layout | 2 | 180 | LOCAL_FIRST | 12 | 52 | 2 | 0 | no | none | yes | 0.72 | 1 | yes | 0 | yes | Fast+strong local pass, no cloud |

## Правило применения в работе

- По умолчанию использовать Cost-First Hybrid.
- Для задач `ui_logic/refactor` fallback в облако разрешен только по формальным триггерам.
- Если acceptance gate не проходит, вернуть cloud-first для `ui_logic/refactor`, local-first оставить для `layout`.

## Harness v2 workflow (обязательно)

1. Smoke:
   - `python harness/run.py --config harness/config.yaml smoke`
2. Логирование боевого/тестового прогона:
   - `python harness/run.py --config harness/config.yaml live ...`
3. Пары LOCAL_FIRST/CLOUD_ONLY:
   - `python harness/run.py --config harness/config.yaml ab`
4. Автогейт:
   - `python harness/run.py --config harness/config.yaml gate`

## Фиксированный бенч-сет (минимум 24 задачи)

- `layout(6)`, `ui_logic(8)`, `bugfix(5)`, `refactor(5)`.
- Шаблон набора: `harness/bench_set.json`.
- Результаты волны не считаются валидными, если:
  - есть `invalid_audit=true` в прогонах;
  - нарушен `max_cloud_calls_per_task`;
  - не выполнен парный прогон LOCAL_FIRST/CLOUD_ONLY для задачи.
