"""Speech Pipeline Auto Diagnosis — Analyze run_log.json as trace data"""

import json
import sys
import os
from collections import Counter

TRACE_LOG_PATH = "trace_output.txt"
RUN_LOG_PATH = "run_log.json"


def run_simulation():
    """运行模拟生成 run_log.json 并捕获输出"""
    print("[DIAG] Running simulate_log.py...")
    ret = os.system("python simulate_log.py > trace_output.txt 2>&1")
    if ret != 0:
        print("[DIAG] ERROR: simulation failed")
        sys.exit(1)


def classify_entry(e):
    """Classify a single log entry as a speech trace result"""
    src = e.get("source", "?")
    prio = e.get("priority", 3)
    played = e.get("played", False)
    drop = e.get("drop_reason", None)
    queued = e.get("queued", False)

    chain = []
    chain.append("SUBMIT")
    chain.append("ARBITRATOR_IN")

    if drop:
        chain.append(f"DROP({drop})")
        return {"result": "DROPPED", "reason": drop, "chain": " -> ".join(chain)}
    if queued and not played:
        chain.append("QUEUED")
        return {"result": "QUEUED_UNPLAYED", "reason": "stuck_in_queue", "chain": " -> ".join(chain)}
    if played:
        chain.append("SELECT")
        chain.append("PLAY")
        chain.append("SPEAK_START")
        chain.append("SPEAK_END")
        return {"result": "SUCCESS", "reason": None, "chain": " -> ".join(chain)}

    chain.append("UNKNOWN")
    return {"result": "UNKNOWN", "reason": None, "chain": " -> ".join(chain)}


def diagnose():
    if not os.path.exists(RUN_LOG_PATH):
        print("[DIAG] run_log.json not found, running simulation...")
        run_simulation()
    elif not os.path.exists(TRACE_LOG_PATH):
        run_simulation()

    with open(RUN_LOG_PATH, "r", encoding="utf-8") as f:
        entries = json.load(f)

    results = []
    stats = Counter()
    vlm_traces = []
    decision_traces = []
    warning_drops = []
    vlm_drops = []

    for i, e in enumerate(entries):
        r = classify_entry(e)
        r["index"] = i
        r["time"] = e["time"]
        r["source"] = e.get("source", "?")
        r["priority"] = e.get("priority", 3)
        r["text"] = e.get("text", "")[:40]
        results.append(r)
        stats[r["result"]] += 1

        if e.get("source") == "vlm":
            vlm_traces.append(r)
        if e.get("source") == "decision":
            decision_traces.append(r)

        if r["result"] == "DROPPED":
            if e.get("priority") == 1:
                warning_drops.append(r)
            if e.get("source") == "vlm":
                vlm_drops.append(r)

    # --- Report ---
    print()
    print("=" * 60)
    print("         [SPEECH TRACE REPORT — Auto Diagnosis]")
    print("=" * 60)
    print()

    total = len(results)
    print(f"  Total entries traced:  {total}")
    print()

    # Statistics
    print("  --- STATISTICS ---")
    for k in ["SUCCESS", "DROPPED", "QUEUED_UNPLAYED"]:
        if stats.get(k, 0) > 0:
            print(f"  {k:20s}: {stats[k]}")
    print()

    # Drop reason breakdown
    drop_reasons = Counter()
    for r in results:
        if r["result"] == "DROPPED":
            drop_reasons[r["reason"]] += 1
    if drop_reasons:
        print("  --- DROP REASONS ---")
        for reason, count in sorted(drop_reasons.items(), key=lambda x: -x[1]):
            print(f"  {reason:35s}: {count}")
        print()

    # Warning drops
    if warning_drops:
        print(f"  --- WARNING DROPS ({len(warning_drops)}) ---")
        print("  CRITICAL: Emergency warnings were discarded!")
        for w in warning_drops[:5]:
            print(f"    t={w['time']:.1f}s reason={w['reason']} text={w['text']}")
        if len(warning_drops) > 5:
            print(f"    ... and {len(warning_drops)-5} more")
        print()

    # VLM analysis
    vlm_total = len(vlm_traces)
    vlm_success = sum(1 for v in vlm_traces if v["result"] == "SUCCESS")
    vlm_dropped = sum(1 for v in vlm_traces if v["result"] == "DROPPED")
    vlm_queued = sum(1 for v in vlm_traces if v["result"] == "QUEUED_UNPLAYED")

    if vlm_total > 0:
        print("  --- VLM ANALYSIS ---")
        print(f"  Total VLM traces:     {vlm_total}")
        print(f"  SUCCESS:              {vlm_success} ({vlm_success/vlm_total*100:.1f}%)")
        print(f"  DROPPED:              {vlm_dropped} ({vlm_dropped/vlm_total*100:.1f}%)")
        print(f"  QUEUED (unplayed):    {vlm_queued} ({vlm_queued/vlm_total*100:.1f}%)")

        vlm_drop_reasons = Counter()
        for v in vlm_traces:
            if v["result"] == "DROPPED":
                vlm_drop_reasons[v["reason"]] += 1
        if vlm_drop_reasons:
            print("  VLM drop reasons:")
            for reason, cnt in sorted(vlm_drop_reasons.items(), key=lambda x: -x[1]):
                print(f"    {reason}: {cnt}")
        print()

        # VLM latency
        vlm_played = [e for e in entries if e.get("source") == "vlm" and e.get("played")]
        if vlm_played:
            delays = []
            for ve in vlm_played:
                req = ve.get("vlm_request_time", ve["time"])
                pt = ve.get("vlm_played_time", ve["time"])
                delays.append(pt - req)
            delays.sort()
            print("  VLM Latency (played):")
            print(f"    count: {len(delays)}")
            print(f"    min:   {min(delays):.1f}s")
            print(f"    p50:   {delays[len(delays)//2]:.1f}s")
            if len(delays) >= 10:
                print(f"    p90:   {delays[int(len(delays)*0.9)]:.1f}s")
            print(f"    max:   {max(delays):.1f}s")
        print()

    # First 5 trace chains
    print("  --- SAMPLE TRACES (first 5 played) ---")
    shown = 0
    for r in results:
        if r["result"] == "SUCCESS" and shown < 5:
            print(f"  [{r['index']:3d}] t={r['time']:.1f}s {r['source']} p={r['priority']}")
            print(f"       {r['chain']}")
            print(f"       text: {r['text']}")
            shown += 1

    # Top root cause
    print()
    print("  --- ROOT CAUSE ---")
    dropped_count = stats.get("DROPPED", 0)
    queued_count = stats.get("QUEUED_UNPLAYED", 0)
    if dropped_count > 0:
        top_reason = drop_reasons.most_common(1)[0]
        print(f"  Primary failure: DROPPED ({dropped_count} entries)")
        print(f"  Top reason: {top_reason[0]} ({top_reason[1]} occurrences)")
    if queued_count > 0:
        print(f"  Secondary: {queued_count} entries stuck in queue at simulation end")

    if warning_drops:
        print()
        print("  !! CRITICAL: WARNING (priority=1) entries were dropped.")
        print("     Safety guarantee violated.")
        for w in warning_drops[:3]:
            print(f"     t={w['time']:.1f}s {w['reason']}")

    print()
    print("=" * 60)
    print("         [DIAGNOSIS COMPLETE]")
    print("=" * 60)


if __name__ == "__main__":
    diagnose()
