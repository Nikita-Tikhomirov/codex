#!/usr/bin/env python3
"""
Local-First Harness v2
- smoke: runtime/model/task checks
- live: normalize + validate one run record and emit audit line
- ab: build paired LOCAL_FIRST/CLOUD_ONLY dataset from run records
- gate: compute acceptance gate summary
"""
from __future__ import annotations

import argparse
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


def ensure_utf8_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_config(path: Path) -> dict[str, Any]:
    # config.yaml is stored as YAML-compatible JSON for zero dependencies.
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


def cmd_live(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    mode = args.mode
    if mode == "AUTO":
        mode = choose_default_mode(args.task_type, args.files_touched, args.risk, args.simple_bugfix, cfg)

    row = {
        "timestamp": now_iso(),
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

    append_jsonl(Path(args.output), row)
    print(audit_line(row))
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


def cmd_ab(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    _ = cfg
    rows = load_jsonl(Path(args.input))
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
    for task_id, m in sorted(by_task.items()):
        if "LOCAL_FIRST" in m and "CLOUD_ONLY" in m:
            pairs.append(
                {
                    "task_id": task_id,
                    "task_type": m["LOCAL_FIRST"].get("task_type") or m["CLOUD_ONLY"].get("task_type"),
                    "local": m["LOCAL_FIRST"],
                    "cloud": m["CLOUD_ONLY"],
                }
            )

    payload = {
        "generated_at": now_iso(),
        "pairs": pairs,
        "pair_count": len(pairs),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
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


def cmd_gate(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    pairs = data.get("pairs", [])

    local_rows = [p["local"] for p in pairs]
    cloud_rows = [p["cloud"] for p in pairs]

    local_summary = summarize_mode(local_rows)
    cloud_summary = summarize_mode(cloud_rows)

    acceptance = cfg["acceptance"]
    cloud_calls_reduction = 0.0
    if cloud_summary["cloud_calls_avg"] > 0:
        cloud_calls_reduction = (cloud_summary["cloud_calls_avg"] - local_summary["cloud_calls_avg"]) / cloud_summary["cloud_calls_avg"]

    success_drop_pp = (cloud_summary["success_rate"] - local_summary["success_rate"]) * 100
    defects_delta = local_summary["defects_avg"] - cloud_summary["defects_avg"]

    local_accept_candidates = [
        r for r in local_rows
        if r.get("task_type") == "layout" or (r.get("task_type") == "bugfix" and parse_bool_yn(str(r.get("simple_bugfix", "no"))))
    ]
    if local_accept_candidates:
        local_accept = mean([
            1.0 if parse_bool_yn(str(r.get("success", "no"))) and safe_float(r.get("cloud_calls")) == 0 else 0.0
            for r in local_accept_candidates
        ])
    else:
        local_accept = 0.0

    gate = {
        "cloud_calls_reduction": round(cloud_calls_reduction, 4),
        "cloud_calls_reduction_ok": cloud_calls_reduction >= float(acceptance["cloud_calls_reduction_min"]),
        "success_drop_pp": round(success_drop_pp, 4),
        "success_ok": success_drop_pp <= float(acceptance["success_rate_drop_max_pp"]),
        "defects_delta": round(defects_delta, 4),
        "defects_ok": defects_delta <= float(acceptance["defects_delta_max"]),
        "local_accept": round(local_accept, 4),
        "local_accept_ok": local_accept >= float(acceptance["local_accept_layout_simple_bugfix_min"]),
    }
    gate["passed"] = all([
        gate["cloud_calls_reduction_ok"],
        gate["success_ok"],
        gate["defects_ok"],
        gate["local_accept_ok"],
    ])

    stability_path = Path(args.stability)
    stability = json.loads(stability_path.read_text(encoding="utf-8-sig")) if stability_path.exists() else {}

    out_payload = {
        "generated_at": now_iso(),
        "local_summary": local_summary,
        "cloud_summary": cloud_summary,
        "gate": gate,
        "stability": stability,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out_payload, ensure_ascii=False, indent=2))
    return 0


def parse_task_status(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out


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

    log_path = Path(smoke_cfg["watchdog_log"])
    if not log_path.is_absolute():
        log_path = Path(args.root) / log_path
    tail = []
    if log_path.exists():
        tail = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-20:]
    report["checks"]["watchdog_log_tail"] = tail

    drive = args.drive.upper()
    if os.name == "nt":
        rc, out, err = run_cmd(["powershell", "-NoProfile", "-Command", f"$d=Get-PSDrive -Name {drive}; Write-Output $d.Free"], timeout=30)
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
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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

    s_ab = sub.add_parser("ab")
    s_ab.add_argument("--input", default="harness/runs.jsonl")
    s_ab.add_argument("--output", default="harness/ab_results.json")

    s_gate = sub.add_parser("gate")
    s_gate.add_argument("--input", default="harness/ab_results.json")
    s_gate.add_argument("--stability", default="harness/stability.json")
    s_gate.add_argument("--output", default="harness/gate_summary.json")

    return p


def main() -> int:
    ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args()
    cfg = load_config(Path(args.config))

    if args.cmd == "smoke":
        return cmd_smoke(args, cfg)
    if args.cmd == "live":
        return cmd_live(args, cfg)
    if args.cmd == "ab":
        return cmd_ab(args, cfg)
    if args.cmd == "gate":
        return cmd_gate(args, cfg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
