"""Phase E1: DROP reason coverage validator.
Checks that all DROP events in trace.jsonl have a non-empty reason field."""

import json
import os
import sys

from validation.result_schema import ValidationResult


def validate_drop_coverage(trace_path=None):
    if trace_path is None:
        trace_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "logs", "trace.jsonl",
        )

    vr = ValidationResult("DROP_REASON_COVERAGE")

    if not os.path.exists(trace_path):
        vr.add_metric("status", "no_trace_file")
        vr.add_metric("note", "run any experiment first to generate trace")
        return vr

    events = []
    with open(trace_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    drops = [e for e in events if e.get("event") in ("DROP", "REQUEUE")]
    vr.add_metric("total_drop_events", len(drops))

    missing_reason = [e for e in drops if not e.get("reason")]
    vr.add_metric("drops_without_reason", len(missing_reason))

    if missing_reason:
        vr.fail(f"{len(missing_reason)} DROP/REQUEUE events missing reason field")
    elif not drops:
        vr.add_metric("note", "no DROP events in trace (low load)")
    else:
        reasons = {}
        for e in drops:
            r = e.get("reason", "unknown")
            reasons[r] = reasons.get(r, 0) + 1
        vr.add_metric("reason_distribution", str(reasons))

    return vr


def validate_trace_completeness(trace_path=None):
    if trace_path is None:
        trace_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "logs", "trace.jsonl",
        )

    vr = ValidationResult("TRACE_COMPLETENESS")

    if not os.path.exists(trace_path):
        vr.add_metric("status", "no_trace_file")
        return vr

    events = []
    with open(trace_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    # SUBMIT should have corresponding SELECT or PLAY or DROP
    tasks = {}
    for e in events:
        tid = e.get("id")
        if not tid:
            continue
        if tid not in tasks:
            tasks[tid] = {"submitted": False, "outcome": set()}
        evt = e.get("event", "")
        if evt == "SUBMIT":
            tasks[tid]["submitted"] = True
        elif evt in ("SELECT", "PLAY", "DROP", "REQUEUE"):
            tasks[tid]["outcome"].add(evt)

    orphaned = [tid for tid, t in tasks.items() if t["submitted"] and not t["outcome"]]
    vr.add_metric("total_submits", sum(1 for t in tasks.values() if t["submitted"]))
    vr.add_metric("orphaned_submits", len(orphaned))

    if orphaned:
        vr.fail(f"{len(orphaned)} SUBMIT events with no SELECT/PLAY/DROP/REQUEUE outcome")
    else:
        vr.add_metric("status", "all_submits_have_outcome")

    return vr
