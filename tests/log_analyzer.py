"""极限压测分析工具：找出系统崩溃阈值"""

import json
from collections import defaultdict


def percentile(data, p):
    if not data:
        return None
    s = sorted(data)
    idx = int(len(s) * p / 100.0)
    idx = min(idx, len(s) - 1)
    return s[idx]


class LogAnalyzer:
    def __init__(self, log_path: str):
        with open(log_path, "r", encoding="utf-8") as f:
            self.logs = json.load(f)

        self.repetitions = []
        self.misses = []
        self.oscillations = []
        self.avg_interval = None
        self.vlm_interrupts = []
        self.vlm_starved = []
        self.order_violations = []
        self.queue_overflows = []
        self.vlm_delay_list = []

        self.max_queue_length = 0
        self.starvation_rate = 0.0
        self.drop_by_priority = {"warning": 0, "vlm": 0, "env": 0}
        self.latency_p50 = None
        self.latency_p90 = None
        self.latency_p99 = None
        self.burst_failures = []
        self.break_points = []

        self.policy_drops = 0
        self.success_rate = 0.0

        self.worst_window_start = None
        self.worst_window_drops = 0
        self.system_state = "GREEN"

    def detect_repetition(self):
        self.repetitions.clear()
        prev_text = None
        for i, entry in enumerate(self.logs):
            spoken = entry.get("played") or entry.get("spoke")
            if not spoken:
                prev_text = None
                continue
            if entry.get("text") == prev_text:
                self.repetitions.append({
                    "index": i,
                    "text": entry["text"],
                    "time": entry["time"],
                })
            prev_text = entry.get("text")

    def detect_miss(self):
        self.misses.clear()
        states = {}
        for entry in self.logs:
            objects = entry.get("objects", [])
            if isinstance(objects, dict):
                objects = objects.get("objects", objects) or []
            if not isinstance(objects, list) or not objects:
                continue
            spoke = entry.get("spoke") or entry.get("played")
            for obj in objects:
                key = (obj.get("class_zh", ""), obj.get("direction", ""))
                dist = obj.get("distance", "")
                if not key[0]:
                    continue
                if key not in states:
                    states[key] = {"last_dist": dist}
                else:
                    prev = states[key]
                    if prev["last_dist"] == "较远" and dist == "很近" and not spoke:
                        self.misses.append({
                            "key": key,
                            "time": entry["time"],
                            "from_dist": prev["last_dist"],
                            "to_dist": dist,
                        })
                    prev["last_dist"] = dist

    def detect_oscillation(self):
        self.oscillations.clear()
        dist_sequences = defaultdict(list)
        for entry in self.logs:
            objects = entry.get("objects", [])
            if isinstance(objects, dict):
                objects = objects.get("objects", objects) or []
            if not isinstance(objects, list) or not objects:
                continue
            for obj in objects:
                key = (obj.get("class_zh", ""), obj.get("direction", ""))
                if key[0]:
                    dist_sequences[key].append(obj.get("distance", ""))
        for key, dists in dist_sequences.items():
            changes = sum(1 for i in range(1, len(dists)) if dists[i] != dists[i - 1])
            if len(dists) > 3 and changes > len(dists) * 0.5:
                self.oscillations.append({
                    "key": key,
                    "total_frames": len(dists),
                    "changes": changes,
                })

    def detect_frequency(self):
        played_times = sorted([
            e.get("_play_time", e["time"])
            for e in self.logs
            if e.get("played") or e.get("spoke")
        ])
        if len(played_times) < 2:
            self.avg_interval = None
            return None
        intervals = [played_times[i] - played_times[i - 1] for i in range(1, len(played_times))]
        self.avg_interval = sum(intervals) / len(intervals) if intervals else None
        return self.avg_interval

    def detect_vlm_interrupt(self):
        self.vlm_interrupts.clear()
        played_entries = sorted(
            [e for e in self.logs if e.get("played") or e.get("spoke")],
            key=lambda x: x.get("_play_time", x["time"])
        )
        for i in range(1, len(played_entries)):
            curr = played_entries[i]
            prev = played_entries[i - 1]
            curr_pt = curr.get("_play_time", curr["time"])
            prev_pt = prev.get("_play_time", prev["time"])
            if curr.get("source") == "vlm" and curr_pt - prev_pt < 1.5:
                self.vlm_interrupts.append({
                    "time": curr["time"],
                    "play_time": curr_pt,
                    "text": curr.get("text", ""),
                    "gap": curr_pt - prev_pt,
                })

    def detect_vlm_starvation(self):
        self.vlm_starved.clear()
        for entry in self.logs:
            if entry.get("source") != "vlm":
                continue
            req_time = entry.get("vlm_request_time", entry["time"])
            played = entry.get("played")
            played_time = entry.get("vlm_played_time")
            if not played:
                self.vlm_starved.append({
                    "request_time": req_time,
                    "text": entry.get("text", ""),
                    "reason": "never_played",
                })
            elif played_time is not None and played_time - req_time > 5.0:
                self.vlm_starved.append({
                    "request_time": req_time,
                    "played_time": played_time,
                    "delay": played_time - req_time,
                })

    def detect_order(self):
        self.order_violations.clear()
        played_entries = sorted(
            [e for e in self.logs if e.get("played") or e.get("spoke")],
            key=lambda x: x.get("_play_time", x["time"])
        )
        WINDOW = 0.5
        for i in range(len(played_entries)):
            a = played_entries[i]
            pa = a.get("priority")
            if pa is None:
                continue
            a_pt = a.get("_play_time", a["time"])
            for j in range(i + 1, len(played_entries)):
                b = played_entries[j]
                b_pt = b.get("_play_time", b["time"])
                if b_pt - a_pt > WINDOW:
                    break
                pb = b.get("priority")
                if pb is None:
                    continue
                if pa > pb and a.get("source") == b.get("source"):
                    self.order_violations.append({
                        "time_first": a["time"],
                        "priority_first": pa,
                        "time_second": b["time"],
                        "priority_second": pb,
                        "is_spike": a.get("is_spike") or b.get("is_spike"),
                    })

    def detect_queue_overflow(self):
        self.queue_overflows.clear()
        overflow_reasons = {"overflow", "overflow_low_priority", "overflow_vlm", "rejected_backpressure"}
        for entry in self.logs:
            if entry.get("drop_reason") in overflow_reasons:
                self.queue_overflows.append({
                    "time": entry["time"],
                    "text": entry.get("text", ""),
                    "source": entry.get("source", ""),
                    "priority": entry.get("priority"),
                    "is_spike": entry.get("is_spike", False),
                    "drop_reason": entry.get("drop_reason"),
                })

    def detect_vlm_delay(self):
        self.vlm_delay_list.clear()
        for entry in self.logs:
            if entry.get("source") != "vlm" or not entry.get("played"):
                continue
            req = entry.get("vlm_request_time")
            played = entry.get("vlm_played_time")
            if req is None or played is None:
                continue
            self.vlm_delay_list.append(played - req)

    # ==================================================================
    # 新增极限指标
    # ==================================================================

    def compute_max_queue(self):
        max_q = 0
        for entry in self.logs:
            qs = entry.get("queue_size", 0)
            if qs > max_q:
                max_q = qs
        self.max_queue_length = max_q

    def compute_starvation_rate(self):
        total_vlm = sum(1 for e in self.logs if e.get("source") == "vlm")
        if total_vlm == 0:
            self.starvation_rate = 0.0
            return
        starved = len(self.vlm_starved)
        self.starvation_rate = starved / total_vlm

    def compute_drop_distribution(self):
        self.drop_by_priority = {"warning": 0, "vlm": 0, "env": 0, "total": 0}
        for entry in self.logs:
            drop = entry.get("drop_reason")
            if not drop:
                continue
            self.drop_by_priority["total"] += 1
            prio = entry.get("priority")
            if prio == 1:
                self.drop_by_priority["warning"] += 1
            elif prio == 2:
                self.drop_by_priority["vlm"] += 1
            elif prio == 3:
                self.drop_by_priority["env"] += 1

    def compute_latency_percentiles(self):
        delays = self.vlm_delay_list
        self.latency_p50 = percentile(delays, 50)
        self.latency_p90 = percentile(delays, 90)
        self.latency_p99 = percentile(delays, 99)

    def compute_burst_failures(self):
        self.burst_failures.clear()
        spike_entries = [e for e in self.logs if e.get("is_spike")]
        if not spike_entries:
            return

        spike_start_times = [0.0, 20.0, 40.0, 60.0, 80.0, 100.0, 115.0]
        for sst in spike_start_times:
            window = [e for e in spike_entries if sst <= e["time"] < sst + 2.5]

            if not window:
                continue

            played = sorted(
                [e for e in window if e.get("played")],
                key=lambda x: x.get("_play_time", x["time"])
            )

            order_issues = 0
            for i in range(1, len(played)):
                a, b = played[i - 1], played[i]
                pa, pb = a.get("priority"), b.get("priority")
                if pa is not None and pb is not None and pa > pb and a.get("source") == b.get("source"):
                    order_issues += 1

            starved_in_window = sum(
                1 for e in window
                if e.get("source") == "vlm" and (
                    not e.get("played")
                    or (e.get("vlm_played_time") or 999) - e.get("vlm_request_time", e["time"]) > 5.0
                )
            )

            self.burst_failures.append({
                "t_start": sst,
                "total": len(window),
                "order_issues": order_issues,
                "vlm_starved": starved_in_window,
            })

    def compute_break_points(self):
        self.break_points.clear()
        entries_sorted = sorted(self.logs, key=lambda x: x["time"])

        first_overflow = None
        first_vlm_expire = None
        first_warning_drop = None

        for e in entries_sorted:
            t = e["time"]
            drop = e.get("drop_reason")
            prio = e.get("priority")
            if drop == "overflow" and first_overflow is None:
                first_overflow = t
            if drop == "expired" and e.get("source") == "vlm" and first_vlm_expire is None:
                first_vlm_expire = t
            if drop and prio == 1 and first_warning_drop is None:
                first_warning_drop = t

        if first_overflow is not None:
            self.break_points.append(("First Queue Overflow", first_overflow))
        if first_vlm_expire is not None:
            self.break_points.append(("First VLM Expired", first_vlm_expire))
        if first_warning_drop is not None:
            self.break_points.append(("First WARNING Drop !!CRITICAL!!", first_warning_drop))

        played_entries = sorted(
            [e for e in self.logs if e.get("played")],
            key=lambda x: x.get("_play_time", x["time"])
        )
        for i in range(1, len(played_entries)):
            a, b = played_entries[i - 1], played_entries[i]
            pa, pb = a.get("priority"), b.get("priority")
            if pa is not None and pb is not None and pa > pb and a.get("source") == b.get("source"):
                a_pt = a.get("_play_time", a["time"])
                b_pt = b.get("_play_time", b["time"])
                if b_pt - a_pt < 0.5:
                    self.break_points.append(("First Order Violation !!CRITICAL!!", b_pt))
                    break

    def compute_worst_window(self):
        entries_sorted = sorted(self.logs, key=lambda x: x["time"])
        window_size = 10.0
        max_drops = 0
        worst_start = 0.0

        t_min = entries_sorted[0]["time"] if entries_sorted else 0
        t_max = entries_sorted[-1]["time"] if entries_sorted else 120

        scan_t = t_min
        while scan_t <= t_max:
            drops_in_window = sum(
                1 for e in entries_sorted
                if scan_t <= e["time"] < scan_t + window_size
                and e.get("drop_reason")
            )
            if drops_in_window > max_drops:
                max_drops = drops_in_window
                worst_start = scan_t
            scan_t += 1.0

        self.worst_window_start = worst_start
        self.worst_window_drops = max_drops

    def classify_system_state(self):
        qo = len(self.queue_overflows)
        vs = len(self.vlm_starved)
        wd = self.drop_by_priority.get("warning", 0)
        ov = len(self.order_violations)

        if qo < 10 and vs <= 1 and wd == 0 and ov == 0:
            self.system_state = "GREEN"
        elif qo <= 30 and vs <= 3 and wd == 0 and ov == 0:
            self.system_state = "YELLOW"
        else:
            self.system_state = "RED"

    # ==================================================================

    def run_all(self):
        self.detect_repetition()
        self.detect_miss()
        self.detect_oscillation()
        self.detect_frequency()
        self.detect_vlm_interrupt()
        self.detect_vlm_starvation()
        self.detect_order()
        self.detect_queue_overflow()
        self.detect_vlm_delay()

        self.compute_max_queue()
        self.compute_starvation_rate()
        self.compute_drop_distribution()
        self.compute_latency_percentiles()
        self.compute_burst_failures()
        self.compute_break_points()
        self.compute_worst_window()
        self.classify_system_state()
        self.compute_success_rate()

        self.print_report()

    def compute_success_rate(self):
        total = len(self.logs)
        played = sum(1 for e in self.logs if e.get("played"))
        self.success_rate = played / total * 100 if total > 0 else 0.0
        self.policy_drops = sum(1 for e in self.logs if e.get("drop_reason") == "rejected_backpressure")

    def print_report(self):
        ov = len(self.order_violations)
        vi = len(self.vlm_interrupts)
        vs = len(self.vlm_starved)
        sr = self.starvation_rate * 100

        total = len(self.logs)
        played = sum(1 for e in self.logs if e.get("played"))

        vlm_total = sum(1 for e in self.logs if e.get("source") == "vlm")
        vlm_played = sum(1 for e in self.logs if e.get("source") == "vlm" and e.get("played"))
        if self.vlm_delay_list:
            avg_vlm_delay = sum(self.vlm_delay_list) / len(self.vlm_delay_list)
        else:
            avg_vlm_delay = 0

        print()
        print("=" * 60)
        print("      OPTIMIZATION BENCHMARK — Before vs After")
        print("=" * 60)
        print()
        print(f"  SUCCESS rate:       {self.success_rate:.1f}%  ({played}/{total} played)")
        print(f"  VLM avg delay:      {avg_vlm_delay:.1f}s  ({vlm_played}/{vlm_total} VLM played)")
        print(f"  queue_overflow:     {len(self.queue_overflows)}")
        print(f"  dropped_by_policy:  {self.policy_drops}")
        print(f"  warning_drop:       {self.drop_by_priority['warning']}")
        print(f"  vlm_starvation:     {vs}  ({sr:.1f}%)")
        print(f"  order_violation:    {ov}")
        print(f"  vlm_interrupt:      {vi}")
        print()

        if self.latency_p50 is not None:
            print(f"  VLM Latency p50:    {self.latency_p50:.1f}s")
        print()

        print(f"  ENV drops:          {self.drop_by_priority['env']}")
        print(f"  max_queue_length:   {self.max_queue_length}")
        state = {"GREEN":"GREEN","YELLOW":"YELLOW","RED":"RED"}.get(self.system_state, "?")
        print(f"  system_state:       {state}")
        print()

        passed = self.drop_by_priority['warning'] == 0 and ov == 0 and sr <= 50.0
        print(f"  FINAL:              {'PASS' if passed else 'FAIL'}")
        print()
        print("=" * 60)
