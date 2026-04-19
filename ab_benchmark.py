#!/usr/bin/env python3
"""
Compute A/B summary and acceptance gate for Cost-First Hybrid benchmarks.

Input JSON format:
{
  "runs": [
    {
      "task_id": "T1",
      "task_type": "layout",
      "mode": "LOCAL_FIRST|CLOUD_ONLY",
      "first_draft_sec": 12.3,
      "ready_sec": 54.0,
      "defects_found": 0,
      "success": true,
      "cloud_calls": 0,
      "local_passes": 2,
      "cloud_fallback": "no",
      "fallback_trigger": "none",
      "retrieval_used": "yes",
      "retrieval_hit_score": 0.71
    }
  ],
  "stability": {
    "watchdog_restarts_day": 0,
    "crash_count": 0,
    "successful_runs_without_manual": 95.0
  }
}
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Summary:
    mode: str
    runs: int
    first_draft_avg: float
    first_draft_median: float
    ready_avg: float
    ready_median: float
    defects_avg: float
    defects_median: float
    success_rate: float
    cloud_calls_avg: float
    cloud_fallback_rate: float


def mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 2) if values else 0.0


def median(values: list[float]) -> float:
    return round(statistics.median(values), 2) if values else 0.0


def to_float(v: object, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def summarize(mode: str, runs: list[dict]) -> Summary:
    drafts = [to_float(r.get("first_draft_sec")) for r in runs]
    ready = [to_float(r.get("ready_sec")) for r in runs]
    defects = [to_float(r.get("defects_found")) for r in runs]
    cloud_calls = [to_float(r.get("cloud_calls")) for r in runs]
    success = [1.0 if bool(r.get("success")) else 0.0 for r in runs]
    fallback = [1.0 if str(r.get("cloud_fallback", "")).lower() == "yes" else 0.0 for r in runs]

    return Summary(
        mode=mode,
        runs=len(runs),
        first_draft_avg=mean(drafts),
        first_draft_median=median(drafts),
        ready_avg=mean(ready),
        ready_median=median(ready),
        defects_avg=mean(defects),
        defects_median=median(defects),
        success_rate=round(mean(success), 4),
        cloud_calls_avg=mean(cloud_calls),
        cloud_fallback_rate=round(mean(fallback), 4),
    )


def local_accept_rate(runs: list[dict]) -> float:
    eligible = [r for r in runs if r.get("task_type") in {"layout", "bugfix"}]
    if not eligible:
        return 0.0
    accepted = 0
    for r in eligible:
        if bool(r.get("success")) and to_float(r.get("cloud_calls")) == 0:
            accepted += 1
    return round(accepted / len(eligible), 4)


def evaluate_acceptance(local: Summary, cloud: Summary, local_runs: list[dict]) -> dict:
    # Requirement 1: cloud_calls reduced by >= 40%
    calls_ok = False
    if cloud.cloud_calls_avg > 0:
        calls_ok = (cloud.cloud_calls_avg - local.cloud_calls_avg) / cloud.cloud_calls_avg >= 0.40
    elif local.cloud_calls_avg == 0:
        calls_ok = True

    # Requirement 2: success_rate not worse by more than 5 pp.
    success_ok = (cloud.success_rate - local.success_rate) <= 0.05

    # Requirement 3: defects not worse by more than +0.3 on average.
    defects_ok = (local.defects_avg - cloud.defects_avg) <= 0.3

    # Requirement 4: local accept >= 80% for layout + simple bugfix.
    local_accept = local_accept_rate(local_runs)
    local_accept_ok = local_accept >= 0.8

    return {
        "cloud_calls_reduction_ok": calls_ok,
        "success_rate_ok": success_ok,
        "defects_ok": defects_ok,
        "local_accept_ok": local_accept_ok,
        "local_accept_rate": local_accept,
        "accepted": all([calls_ok, success_ok, defects_ok, local_accept_ok]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B benchmark evaluator")
    parser.add_argument("--input", default="ab_results.json")
    parser.add_argument("--output", default="ab_summary.json")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    runs = payload.get("runs", [])
    local_runs = [r for r in runs if r.get("mode") == "LOCAL_FIRST"]
    cloud_runs = [r for r in runs if r.get("mode") == "CLOUD_ONLY"]
    if not local_runs or not cloud_runs:
        out = {
            "summary": {},
            "acceptance": {
                "accepted": False,
                "reason": "Need both LOCAL_FIRST and CLOUD_ONLY runs",
            },
            "stability": payload.get("stability", {}),
        }
        Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    local_summary = summarize("LOCAL_FIRST", local_runs)
    cloud_summary = summarize("CLOUD_ONLY", cloud_runs)
    acceptance = evaluate_acceptance(local_summary, cloud_summary, local_runs)
    stability = payload.get("stability", {})

    out = {
        "summary": {
            "LOCAL_FIRST": local_summary.__dict__,
            "CLOUD_ONLY": cloud_summary.__dict__,
        },
        "acceptance": acceptance,
        "stability": stability,
    }

    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
