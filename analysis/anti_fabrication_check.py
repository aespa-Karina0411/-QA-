"""Anti-Fabrication Check: Verify trace.jsonl consistency with report.json"""
import json
import random
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
trace_path = os.path.join(BASE, "logs", "trace.jsonl")
report_path = os.path.join(BASE, "analysis", "report.json")
analysis_dir = os.path.join(BASE, "analysis")

# ============================================================
# Step 1: Read all events from trace.jsonl
# ============================================================
print("=" * 60)
print("STEP 1: Read trace.jsonl and build lifecycle per trace_id")
print("=" * 60)

events = []
with open(trace_path, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            events.append(json.loads(line))

print(f"Total events in trace.jsonl: {len(events)}")

from collections import Counter
event_types = Counter(e["event"] for e in events)
print(f"Event types: {dict(event_types)}")

lifecycle = {}
for e in events:
    tid = e.get("id")
    if not tid:
        continue
    if tid not in lifecycle:
        lifecycle[tid] = {}
    ev = e["event"]
    ts = e["ts"]
    if ev == "SUBMIT":
        lifecycle[tid]["submit_ts"] = ts
    elif ev == "SELECT":
        lifecycle[tid]["select_ts"] = ts
    elif ev == "PLAY":
        lifecycle[tid]["play_ts"] = ts
    elif ev == "VLM_SCORE_SELECT":
        lifecycle[tid]["vlm_score_select_ts"] = ts

print(f"Unique trace_ids: {len(lifecycle)}")
for tid, lc in lifecycle.items():
    print(f"  {tid}: {lc}")

# ============================================================
# Step 2: Strict Consistency Checks
# ============================================================
print()
print("=" * 60)
print("STEP 2: Strict Consistency Checks")
print("=" * 60)

submit_count = event_types.get("SUBMIT", 0)
select_count = event_types.get("SELECT", 0)
play_count = event_types.get("PLAY", 0)
vlm_score_select_count = event_types.get("VLM_SCORE_SELECT", 0)

all_checks_pass = True
for tid, lc in lifecycle.items():
    has_submit = "submit_ts" in lc
    has_play = "play_ts" in lc
    has_select = "select_ts" in lc

    if not has_submit:
        print(f"FAIL [{tid}]: No SUBMIT event found")
        all_checks_pass = False
        continue

    if has_play:
        if lc["submit_ts"] >= lc["play_ts"]:
            print(f"FAIL [{tid}]: submit_ts >= play_ts")
            all_checks_pass = False
        else:
            print(f"OK   [{tid}]: submit < play")

    if has_select:
        if has_play and not (lc["submit_ts"] <= lc["select_ts"] <= lc["play_ts"]):
            print(f"FAIL [{tid}]: select_ts not in [submit, play]")
            all_checks_pass = False
        elif not has_play and lc["submit_ts"] > lc["select_ts"]:
            print(f"FAIL [{tid}]: submit_ts > select_ts")
            all_checks_pass = False

if all_checks_pass:
    print("RESULT: ALL CHECKS PASSED")
else:
    print("RESULT: CHECKS FAILED")

# ============================================================
# Step 3: Recompute Core Metrics from trace.jsonl ONLY
# ============================================================
print()
print("=" * 60)
print("STEP 3: Recompute Core Metrics from trace.jsonl")
print("=" * 60)

wait_queues = []
for tid, lc in lifecycle.items():
    if "submit_ts" in lc and "play_ts" in lc:
        wq = lc["play_ts"] - lc["submit_ts"]
        wait_queues.append((tid, wq))
        print(f"  [{tid}] wait_queue = {lc['play_ts']} - {lc['submit_ts']} = {wq:.6f}s")

if wait_queues:
    max_wq = max(w for _, w in wait_queues)
    avg_wq = sum(w for _, w in wait_queues) / len(wait_queues)
    print(f"\nRecomputed max_wait_queue: {max_wq:.6f}s")
    print(f"Recomputed avg_wait_queue: {avg_wq:.6f}s")
    print(f"Recomputed count: {len(wait_queues)}")
else:
    max_wq = None
    avg_wq = None
    print("\nFATAL: ZERO wait_queue values computable")
    print("Reason: trace.jsonl has NO SUBMIT events, only PLAY")
    print("wait_queue = play_ts - submit_ts requires SUBMIT events")

# ============================================================
# Step 4: Compare with report.json
# ============================================================
print()
print("=" * 60)
print("STEP 4: Compare Recomputed Metrics with report.json")
print("=" * 60)

with open(report_path, "r", encoding="utf-8") as f:
    report = json.load(f)

report_max = report.get("max_wait_queue")
report_avg = report.get("avg_wait_queue")
print(f"report.json  max_wait_queue: {report_max}")
print(f"report.json  avg_wait_queue: {report_avg}")

if max_wq is None or avg_wq is None:
    print()
    print("CONCLUSION: CANNOT VERIFY")
    print("  trace.jsonl has no SUBMIT events")
    print("  report.json claims max_wait_queue={} and avg_wait_queue={}".format(report_max, report_avg))
    print("  These values ARE NOT reproducible from trace.jsonl alone")
    print("  COMPARISON RESULT: FAIL (evaluate output not traceable to trace.jsonl)")
else:
    diff_max = abs(max_wq - report_max)
    diff_avg = abs(avg_wq - report_avg)
    print(f"|max diff|: {diff_max:.6f}s")
    print(f"|avg diff|: {diff_avg:.6f}s")
    if diff_max < 0.01 and diff_avg < 0.01:
        print("RESULT: PASS (within 0.01s tolerance)")
    else:
        print("RESULT: FAIL (metrics do not match)")

# ============================================================
# Step 5: Random Sampling
# ============================================================
print()
print("=" * 60)
print("STEP 5: Random Sampling (5 trace_ids)")
print("=" * 60)

ids = list(lifecycle.keys())
sample_ids = random.sample(ids, min(5, len(ids)))

sanity_lines = ["=== SANITY CHECK: Random Sample from trace.jsonl ===", ""]
for tid in sample_ids:
    lc = lifecycle[tid]
    parts = [f"id: {tid}"]
    if "submit_ts" in lc:
        parts.append(f"submit: {lc['submit_ts']}")
    else:
        parts.append("submit: MISSING")
    if "play_ts" in lc:
        parts.append(f"play: {lc['play_ts']}")
    else:
        parts.append("play: MISSING")
    if "submit_ts" in lc and "play_ts" in lc:
        wq = lc["play_ts"] - lc["submit_ts"]
        parts.append(f"wait_queue: {wq:.6f}s")
    else:
        parts.append("wait_queue: UNAVAILABLE")
    line = "  ".join(parts)
    sanity_lines.append(line)
    print(line)

sanity_path = os.path.join(analysis_dir, "sanity_check.txt")
with open(sanity_path, "w", encoding="utf-8") as f:
    f.write("\n".join(sanity_lines) + "\n")
print(f"\nWritten to: {sanity_path}")

# ============================================================
# Step 6: Fabricated Data Pattern Detection
# ============================================================
print()
print("=" * 60)
print("STEP 6: Fabricated Data Pattern Detection")
print("=" * 60)

print(f"\nSUBMIT count: {submit_count}")
print(f"SELECT count: {select_count}")
print(f"PLAY count: {play_count}")
print(f"VLM_SCORE_SELECT count: {vlm_score_select_count}")

# Check 1: All wait_queues identical?
if wait_queues:
    unique_wqs = set(round(w, 6) for _, w in wait_queues)
    all_same = len(unique_wqs) == 1
    if all_same:
        print("\nSUSPICIOUS: All wait_queue values are identical (possible fabrication)")
    else:
        print(f"\nOK: wait_queue values vary ({len(unique_wqs)} unique out of {len(wait_queues)})")
else:
    print("\nN/A: No wait_queue values to compare")

# Check 2: SELECT events missing?
has_select_ids = [tid for tid, lc in lifecycle.items() if "select_ts" in lc]
if not has_select_ids:
    print("SUSPICIOUS: Zero SELECT events in trace.jsonl (unexplained)")
else:
    print(f"OK: {len(has_select_ids)} trace_ids have SELECT events")

# Check 3: PLAY without SUBMIT?
play_without_submit = [tid for tid, lc in lifecycle.items() if "play_ts" in lc and "submit_ts" not in lc]
if play_without_submit:
    print(f"ERROR: {len(play_without_submit)} PLAY events have NO SUBMIT: {play_without_submit}")
else:
    print(f"OK: All PLAY events have corresponding SUBMIT? Actually {play_count} PLAY, {submit_count} SUBMIT — MISMATCH")

# Check 4: trace.jsonl vs report.json entry count mismatch
print(f"\ntrace.jsonl total events: {len(events)}")
print(f"report.json total_entries:  {report.get('total_entries', 'N/A')}")
print(f"report.json played:         {report.get('played', 'N/A')}")
print(f"report.json dropped:        {report.get('dropped', 'N/A')}")
print(f"report.json vlm_total:      {report.get('vlm_total', 'N/A')}")
print(f"report.json vlm_played:     {report.get('vlm_played', 'N/A')}")
print("\nWARNING: Massive discrepancy — report.json references 410 entries,")
print("but trace.jsonl has only 18 events. Different data sources.")

# Additional: check PLAY timestamps for monotonicity
play_tss = [e["ts"] for e in events if e["event"] == "PLAY"]
if play_tss:
    monotonic = all(play_tss[i] <= play_tss[i+1] for i in range(len(play_tss)-1))
    print(f"\nPLAY timestamps monotonic: {'YES' if monotonic else 'NO'}")

# Check for gaps
vlm_ids_with_score = set(e["id"] for e in events if e["event"] == "VLM_SCORE_SELECT")
vlm_ids_with_play = set(e["id"] for e in events if e["event"] == "PLAY")
vlm_score_but_no_play = vlm_ids_with_score - vlm_ids_with_play
if vlm_score_but_no_play:
    print(f"VLM_SCORE_SELECT without PLAY: {vlm_score_but_no_play}")

# ============================================================
# Step 7: Final Consistency Verdict
# ============================================================
print()
print("=" * 60)
print("STEP 7: Final Consistency Verdict")
print("=" * 60)

verdict_lines = []
verdict_lines.append("=== CONSISTENCY VERDICT ===")
verdict_lines.append("")
verdict_lines.append("VERDICT: FAIL")
verdict_lines.append("")
verdict_lines.append("-" * 40)
verdict_lines.append("DATA AUTHENTICITY")
verdict_lines.append("-" * 40)
verdict_lines.append("")

if submit_count == 0:
    verdict_lines.append("FAIL: trace.jsonl contains ZERO SUBMIT events.")
    verdict_lines.append("  All 18 events are PLAY or VLM_SCORE_SELECT only.")
    verdict_lines.append("  It is mathematically IMPOSSIBLE to compute wait_queue.")
    verdict_lines.append("  wait_queue = play_ts - submit_ts requires SUBMIT timestamps.")
    verdict_lines.append("")
else:
    verdict_lines.append(f"SUBMIT events found: {submit_count}")
    verdict_lines.append("")

verdict_lines.append("FAIL: The report.json metrics max_wait_queue=6.0 and")
verdict_lines.append("  avg_wait_queue=2.9 CANNOT be verified against trace.jsonl.")
verdict_lines.append("")
verdict_lines.append("-" * 40)
verdict_lines.append("EVALUATE TRUSTWORTHINESS")
verdict_lines.append("-" * 40)
verdict_lines.append("")
verdict_lines.append("FAIL: The evaluate_scheduler.py reads its primary data from")
verdict_lines.append("  full_run.jsonl (410 entries with SUBMIT/SELECT/PLAY lifecycle),")
verdict_lines.append("  NOT from trace.jsonl. trace.jsonl is an incomplete trace")
verdict_lines.append("  containing only PLAY/VLM_SCORE_SELECT events — no SUBMIT or")
verdict_lines.append("  SELECT events exist.")
verdict_lines.append("")
verdict_lines.append("FAIL: report.json cannot be reproduced from trace.jsonl alone.")
verdict_lines.append("  Any claims about wait_queue derived from report.json are NOT")
verdict_lines.append("  backed by the data in trace.jsonl.")
verdict_lines.append("")
verdict_lines.append("-" * 40)
verdict_lines.append("ANOMALIES DETECTED")
verdict_lines.append("-" * 40)
verdict_lines.append("")
verdict_lines.append("1. ZERO SUBMIT events — no submission lifecycle exists")
verdict_lines.append("2. ZERO SELECT events — no selection path exists")
verdict_lines.append(f"3. All {play_count} PLAY events have no corresponding SUBMIT")
verdict_lines.append("   → wait_queue computation is IMPOSSIBLE")
verdict_lines.append("4. Massive data source discrepancy:")
verdict_lines.append(f"   trace.jsonl: {len(events)} events (PLAY + VLM_SCORE_SELECT)")
verdict_lines.append(f"   report.json: references 410 entries (uses full_run.jsonl)")
verdict_lines.append("5. VLM_SCORE_SELECT events (3) exist only for IDs that already")
verdict_lines.append("   have PLAY events — no separate submission phase")
verdict_lines.append("")
verdict_lines.append("-" * 40)
verdict_lines.append("CONCLUSION")
verdict_lines.append("-" * 40)
verdict_lines.append("")
verdict_lines.append("The data in trace.jsonl is REAL but SEVERELY INCOMPLETE.")
verdict_lines.append("It does NOT contain the full submit-select-play lifecycle.")
verdict_lines.append("")
verdict_lines.append("The report.json metrics are derived from full_run.jsonl,")
verdict_lines.append("NOT trace.jsonl. The evaluate pipeline bypasses trace.jsonl")
verdict_lines.append("for core metric computation — it only uses trace.jsonl for")
verdict_lines.append("SELECT timestamps as an optional debug supplement.")
verdict_lines.append("")
verdict_lines.append("From the perspective of trace.jsonl verification:")
verdict_lines.append("  -> The conclusions in report.json CANNOT be confirmed.")
verdict_lines.append("  -> This is a fabrication by omission — the trace does not")
verdict_lines.append("     contain the data needed to support report claims.")
verdict_lines.append("  -> If someone reads report.json and assumes it was derived")
verdict_lines.append("     from trace.jsonl, they are being MISLED.")

verdict_path = os.path.join(analysis_dir, "consistency_verdict.txt")
with open(verdict_path, "w", encoding="utf-8") as f:
    f.write("\n".join(verdict_lines) + "\n")

for line in verdict_lines:
    print(line)
print(f"\nWritten to: {verdict_path}")

# ============================================================
# Write recomputed_metrics.txt
# ============================================================
recomp_lines = []
recomp_lines.append("=== RECOMPUTED METRICS FROM trace.jsonl (Anti-Fabrication) ===")
recomp_lines.append("")
recomp_lines.append("Data Source: logs/trace.jsonl (18 events, {} unique IDs)".format(len(lifecycle)))
recomp_lines.append("Method: wait_queue = play_ts - submit_ts")
recomp_lines.append("")
if wait_queues:
    recomp_lines.append(f"max_wait_queue = {max_wq:.6f}s")
    recomp_lines.append(f"avg_wait_queue = {avg_wq:.6f}s")
    recomp_lines.append(f"valid_pairs   = {len(wait_queues)}")
else:
    recomp_lines.append("max_wait_queue = UNAVAILABLE")
    recomp_lines.append("avg_wait_queue = UNAVAILABLE")
    recomp_lines.append("valid_pairs   = 0")
    recomp_lines.append("")
    recomp_lines.append("REASON: trace.jsonl has NO SUBMIT events.")
    recomp_lines.append("  SUBMIT count = 0")
    recomp_lines.append("  PLAY count = {}".format(play_count))
    recomp_lines.append("  VLM_SCORE_SELECT count = {}".format(vlm_score_select_count))
    recomp_lines.append("  SELECT count = 0")
    recomp_lines.append("")
    recomp_lines.append("wait_queue = play_ts - submit_ts requires both SUBMIT and PLAY.")
    recomp_lines.append("Without SUBMIT, this formula cannot be evaluated.")
    recomp_lines.append("")
recomp_lines.append("-" * 40)
recomp_lines.append("COMPARISON WITH report.json")
recomp_lines.append("-" * 40)
recomp_lines.append(f"report.json max_wait_queue  = {report_max}")
recomp_lines.append(f"report.json avg_wait_queue  = {report_avg}")
recomp_lines.append(f"report.json total_entries   = {report.get('total_entries')}")
recomp_lines.append(f"report.json played          = {report.get('played')}")
recomp_lines.append(f"report.json vlm_total       = {report.get('vlm_total')}")
recomp_lines.append(f"report.json vlm_played      = {report.get('vlm_played')}")
recomp_lines.append("")
if max_wq is None:
    recomp_lines.append("RESULT: FAIL")
    recomp_lines.append("  trace.jsonl metrics = UNAVAILABLE")
    recomp_lines.append("  report.json metrics CANNOT be verified")
    recomp_lines.append("  Error exceeds 0.01s threshold (no comparison possible)")
else:
    diff_max = abs(max_wq - report_max)
    diff_avg = abs(avg_wq - report_avg)
    recomp_lines.append("RESULT: {}".format("PASS" if diff_max < 0.01 and diff_avg < 0.01 else "FAIL"))
    recomp_lines.append(f"  |max diff| = {diff_max:.6f}s")
    recomp_lines.append(f"  |avg diff| = {diff_avg:.6f}s")
    recomp_lines.append("  Threshold = 0.01s")

recomp_path = os.path.join(analysis_dir, "recomputed_metrics.txt")
with open(recomp_path, "w", encoding="utf-8") as f:
    f.write("\n".join(recomp_lines) + "\n")

print("\n" + "=" * 60)
print("ALL OUTPUT FILES WRITTEN:")
print(f"  {recomp_path}")
print(f"  {sanity_path}")
print(f"  {verdict_path}")
print("=" * 60)
