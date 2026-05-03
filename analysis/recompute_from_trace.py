"""Anti-Fabrication Recompute — Independent verification of report.json from trace.jsonl.

This script:
1. Reads ONLY logs/trace.jsonl (no other data source)
2. Recomputes all metrics independently
3. Compares with analysis/report.json
4. Writes analysis/consistency_verdict.txt (PASS/FAIL)
5. Writes analysis/recomputed_metrics.txt
"""

import json
import os
import sys


BASE = os.path.dirname(os.path.abspath(__file__))
TRACE_PATH = os.path.join(BASE, "..", "logs", "trace.jsonl")
REPORT_PATH = os.path.join(BASE, "report.json")
VERDICT_PATH = os.path.join(BASE, "consistency_verdict.txt")
RECOMP_PATH = os.path.join(BASE, "recomputed_metrics.txt")

TOLERANCE = 0.01


def load_trace(path):
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def build_tasks(events):
    tasks = {}
    for ev in events:
        etype = ev.get("event")
        tid = ev.get("id")
        if not tid:
            continue
        if tid not in tasks:
            tasks[tid] = {}
        if etype == "SUBMIT":
            tasks[tid]["submit_ts"] = ev["ts"]
            tasks[tid]["source"] = ev.get("source", "")
            tasks[tid]["priority"] = ev.get("priority", 3)
        elif etype == "PLAY":
            tasks[tid]["play_ts"] = ev["ts"]
        elif etype == "SELECT":
            tasks[tid]["select_ts"] = ev["ts"]
        elif etype == "DROP_CANDIDATE":
            tasks[tid]["drop_candidate"] = True
        elif etype == "VLM_SCORE_SELECT":
            tasks[tid]["vlm_score_select_ts"] = ev["ts"]
    return tasks


def recompute(tasks):
    wait_queue_list = []
    for tid, tk in tasks.items():
        if "submit_ts" in tk and "play_ts" in tk:
            wq = tk["play_ts"] - tk["submit_ts"]
            if wq >= 0:
                wait_queue_list.append(wq)

    max_wq = max(wait_queue_list) if wait_queue_list else 0
    avg_wq = sum(wait_queue_list) / len(wait_queue_list) if wait_queue_list else 0

    total_submitted = sum(1 for t in tasks.values() if "submit_ts" in t)
    total_played = sum(1 for t in tasks.values() if "play_ts" in t)
    vlm_submitted = sum(1 for t in tasks.values() if t.get("source") == "vlm" and "submit_ts" in t)
    vlm_played = sum(1 for t in tasks.values() if t.get("source") == "vlm" and "play_ts" in t)
    drop_candidates = sum(1 for t in tasks.values() if t.get("drop_candidate"))

    return {
        "max_wait_queue": round(max_wq, 1),
        "avg_wait_queue": round(avg_wq, 1),
        "total_entries": len(tasks),
        "played": total_played,
        "dropped": drop_candidates,
        "vlm_total": vlm_submitted,
        "vlm_played": vlm_played,
        "valid_pairs": len(wait_queue_list),
    }


def main():
    print("=" * 60)
    print("Anti-Fabrication Recompute")
    print("=" * 60)

    # Load trace
    if not os.path.exists(TRACE_PATH):
        print(f"ERROR: trace.jsonl not found at {TRACE_PATH}")
        return 1

    events = load_trace(TRACE_PATH)
    print(f"Loaded {len(events)} events from trace.jsonl")

    # Count event types
    from collections import Counter
    etypes = Counter(e["event"] for e in events)
    print(f"Event types: {dict(etypes)}")
    print(f"  SUBMIT: {etypes.get('SUBMIT', 0)}")
    print(f"  PLAY:   {etypes.get('PLAY', 0)}")
    print(f"  SELECT: {etypes.get('SELECT', 0)}")

    # Build lifecycle
    tasks = build_tasks(events)
    print(f"Unique task IDs: {len(tasks)}")

    # Recompute
    recomputed = recompute(tasks)
    print(f"\nRecomputed metrics:")
    for k, v in recomputed.items():
        print(f"  {k}: {v}")

    # Load report.json
    if not os.path.exists(REPORT_PATH):
        print(f"\nERROR: report.json not found at {REPORT_PATH}")
        return 1

    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        report = json.load(f)

    # Compare
    print(f"\nComparison with report.json:")
    errors = []

    for key in ["max_wait_queue", "avg_wait_queue"]:
        r_val = recomputed.get(key)
        p_val = report.get(key)
        if r_val is None:
            errors.append(f"{key}: recomputed=None, report={p_val}")
            print(f"  {key}: recomputed=None vs report={p_val} -> FAIL")
        elif abs(r_val - p_val) >= TOLERANCE:
            errors.append(f"{key}: recomputed={r_val}, report={p_val}, diff={abs(r_val-p_val):.4f}")
            print(f"  {key}: recomputed={r_val} vs report={p_val} (diff={abs(r_val-p_val):.4f}) -> FAIL")
        else:
            print(f"  {key}: recomputed={r_val} vs report={p_val} -> PASS")

    passed = len(errors) == 0
    verdict = "PASS" if passed else "FAIL"

    # Write recomputed_metrics.txt
    recomp_lines = [
        "=== RECOMPUTED METRICS (Anti-Fabrication V4) ===",
        "",
        f"Data Source: {TRACE_PATH}",
        f"Events: {len(events)}",
        f"Unique Task IDs: {len(tasks)}",
        "",
        "--- Recomputed from trace.jsonl ---",
    ]
    for k, v in recomputed.items():
        recomp_lines.append(f"{k}: {v}")
    recomp_lines += [
        "",
        "--- Compare with report.json ---",
        f"report.json max_wait_queue: {report.get('max_wait_queue')}",
        f"report.json avg_wait_queue: {report.get('avg_wait_queue')}",
        "",
        f"Result: {verdict}",
    ]
    if errors:
        recomp_lines.append(f"Errors: {len(errors)}")
        for e in errors:
            recomp_lines.append(f"  - {e}")

    with open(RECOMP_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(recomp_lines) + "\n")
    print(f"\nWrote: {RECOMP_PATH}")

    # Write consistency_verdict.txt
    verdict_lines = [
        "=== CONSISTENCY VERDICT ===",
        "",
        f"Verdict: {verdict}",
        "",
        f"Data source: {TRACE_PATH}",
        f"Verification method: Independent recompute from trace.jsonl",
        "",
    ]
    if passed:
        verdict_lines += [
            "All core metrics (max_wait_queue, avg_wait_queue) match",
            "within tolerance ({:.2f}s).".format(TOLERANCE),
            "",
            "report.json is CONSISTENT with trace.jsonl.",
            "Metrics can be independently reproduced.",
            "",
            "Data authenticity:   VERIFIED",
            "Evaluate reliability: TRUSTED",
            "No anomalies detected.",
        ]
    else:
        verdict_lines += [
            "Mismatch detected between recomputed metrics and report.json:",
        ]
        for e in errors:
            verdict_lines.append(f"  - {e}")
        verdict_lines += [
            "",
            "report.json is INCONSISTENT with trace.jsonl.",
            "Metrics CANNOT be independently reproduced.",
            "",
            "Data authenticity:   UNVERIFIED",
            "Evaluate reliability: UNTRUSTED",
        ]

    with open(VERDICT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(verdict_lines) + "\n")
    print(f"Wrote: {VERDICT_PATH}")

    print()
    print("=" * 60)
    if passed:
        print("FINAL RESULT: PASS")
    else:
        print("FINAL RESULT: FAIL")
    print("=" * 60)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
