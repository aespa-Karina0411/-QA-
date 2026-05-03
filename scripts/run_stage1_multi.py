"""
Stage 1 多轮自动化实验（3 round × 3 load = 9 trace）
====================================================
在单轮实验基础上增加：
  - 3 轮重复
  - 直接解析 trace 提取指标（不依赖 evaluate_scheduler 文本输出）
  - 精确相等一致性检查
  - 输出 stage1_summary.csv
"""

import csv
import json
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from perception import speech_arbitrator as sa_module
from perception.speech_arbitrator import SpeechArbitrator
from observe.trace_logger import TraceLogger
from core.global_config import CONFIG

PLAY_DURATION = float(CONFIG.get("speech.play_duration", 2.5))
LOG_DIR = os.path.join(ROOT, "logs")

STAGE1_TEXTS = [
    "帮我看看前面有什么", "右边安全吗", "能描述一下环境吗",
    "有没有障碍物", "前面有什么？", "左边那个是什么？",
    "有没有危险？", "路口怎么过？", "红绿灯是什么颜色？",
    "前方还有多远？",
]


class Stage1Runner:
    def __init__(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        self.trace_path = os.path.join(LOG_DIR, "trace.jsonl")
        if os.path.exists(self.trace_path):
            os.remove(self.trace_path)

        self.trace = TraceLogger(self.trace_path)
        sa_module._trace_logger = self.trace
        self.arb = SpeechArbitrator()
        self.arb.trace = self.trace

        self.playing_until = None
        self.submitted = 0
        self.played = 0

        self.context = {
            "system": {
                "user_focus": {
                    "active": False,
                    "enter_ts": 0.0,
                    "timeout": CONFIG.get("user_focus.timeout", 5.0),
                }
            }
        }

    def submit(self, text, source="user_direct", priority=2):
        tid = f"s1-{uuid.uuid4().hex[:6]}"
        self.arb.submit(
            {
                "trace_id": tid,
                "source": source,
                "priority": priority,
                "text": text,
                "time": time.time(),
            },
            context=self.context,
        )
        self.submitted += 1

    def tick(self):
        if self.playing_until is not None and time.time() < self.playing_until:
            return False
        self.playing_until = None

        item = self.arb.select_next()
        if item is None:
            return False

        play_time = time.time()
        self.trace.log(
            "PLAY",
            id=item.get("trace_id"),
            source=item.get("source"),
            priority=item.get("priority", 3),
        )
        if item.get("source") == "vlm":
            self.arb.mark_vlm_played()

        self.playing_until = play_time + PLAY_DURATION
        self.played += 1
        return True

    def drain_all(self):
        deadline = time.time() + 120
        while time.time() < deadline:
            if not self.tick():
                if self.playing_until is not None:
                    time.sleep(min(self.playing_until - time.time(), 0.1))
                    continue
                if not (
                    self.arb.warning_queue
                    or self.arb.vlm_queue
                    or self.arb.env_queue
                ):
                    break
                time.sleep(0.05)

    def save_to(self, path):
        import shutil
        shutil.copy(self.trace_path, path)
        return path


# ── 实验函数（可指定输出路径）──────────────────────────────

def _experiment(runner, load_type, interval, duration, texts, source="user_direct"):
    deadline = time.time() + duration
    next_submit = time.time()
    idx = 0

    while time.time() < deadline:
        runner.tick()
        if time.time() >= next_submit:
            runner.submit(texts[idx % len(texts)], source=source)
            idx += 1
            next_submit = time.time() + interval
        time.sleep(0.05)

    runner.drain_all()


def _experiment_burst(runner):
    for round_n in range(3):
        for i in range(10):
            runner.submit(f"burst_r{round_n + 1}_{i + 1}")
            time.sleep(0.1)
            runner.tick()
        pause_until = time.time() + 10.0
        while time.time() < pause_until:
            runner.tick()
            time.sleep(0.05)
    runner.drain_all()


def run_one_round(run_id):
    paths = {}
    metrics = {}

    for load, label in [("low", "low"), ("mid", "mid"), ("burst", "burst")]:
        r = Stage1Runner()

        if load == "low":
            _experiment(r, "low", 5.0, 120.0, STAGE1_TEXTS)
        elif load == "mid":
            _experiment(r, "mid", 2.0, 120.0, STAGE1_TEXTS, source="vlm")
        else:
            _experiment_burst(r)

        out_path = os.path.join(LOG_DIR, f"stage1_run{run_id}_{label}.jsonl")
        r.save_to(out_path)
        paths[label] = out_path

        m = analyze_trace(out_path)
        metrics[label] = m
        print(
            f"  {label:6s}  submit={m['submit']:>3d}  play={m['play']:>3d}  "
            f"rate={m['play_rate']:.3f}  avg_wait={m['avg_wait']:.3f}s  "
            f"max_wait={m['max_wait']:.3f}s"
        )

    return metrics


# ── trace 直接解析（不调用 evaluate_scheduler）─────────────

def analyze_trace(path):
    with open(path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    submits = sum(1 for e in events if e.get("event") == "SUBMIT")
    plays = sum(1 for e in events if e.get("event") == "PLAY")

    tasks = {}
    for e in events:
        tid = e.get("id")
        if not tid:
            continue
        if tid not in tasks:
            tasks[tid] = {}
        if e["event"] == "SUBMIT":
            tasks[tid]["submit_ts"] = e["ts"]
        if e["event"] == "PLAY":
            tasks[tid]["play_ts"] = e["ts"]

    waits = []
    for t in tasks.values():
        if "submit_ts" in t and "play_ts" in t:
            w = t["play_ts"] - t["submit_ts"]
            if w >= 0:
                waits.append(w)

    return {
        "submit": submits,
        "play": plays,
        "play_rate": round(plays / submits, 3) if submits else 0.0,
        "avg_wait": round(sum(waits) / len(waits), 3) if waits else 0.0,
        "max_wait": round(max(waits), 3) if waits else 0.0,
    }


# ── 一致性检查（精确相等）────────────────────────────────

def check_consistency(all_metrics):
    print("\n=== CONSISTENCY CHECK ===")
    loads = ["low", "mid", "burst"]
    all_pass = True

    for load in loads:
        rates = [all_metrics[r][load]["play_rate"] for r in range(1, 4)]
        if len(set(rates)) != 1:
            print(f"  [FAIL] {load}: play_rates differ across runs: {rates}")
            all_pass = False
        else:
            print(f"  [PASS] {load}: play_rate = {rates[0]:.3f} (identical across 3 runs)")

    if all_pass:
        print("\n  [PASS] Stage 1 behavior consistent")
    else:
        print("\n  [FAIL] Stage 1 behavior unstable")
    return all_pass


# ── 生成 CSV ──────────────────────────────────────────────

def write_csv(all_metrics, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run", "load", "submit", "play", "play_rate", "avg_wait", "max_wait"])
        for run_id in range(1, 4):
            for load in ["low", "mid", "burst"]:
                m = all_metrics[run_id][load]
                w.writerow([run_id, load, m["submit"], m["play"], m["play_rate"], m["avg_wait"], m["max_wait"]])
    print(f"\nCSV saved: {path}")


# ── main ──────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Stage 1 多轮实验 (3 round × 3 load)")
    print("=" * 60)

    t0 = time.time()
    all_metrics = {}

    for run_id in range(1, 4):
        print(f"\n--- Run {run_id} ---")
        all_metrics[run_id] = run_one_round(run_id)

    elapsed = time.time() - t0

    # 终端汇总
    print(f"\n{'=' * 60}")
    print(f"  全部完成，总耗时 {elapsed:.0f}s")
    for run_id in range(1, 4):
        m = all_metrics[run_id]
        parts = [
            f"low={m['low']['play_rate']:.3f}",
            f"mid={m['mid']['play_rate']:.3f}",
            f"burst={m['burst']['play_rate']:.3f}",
        ]
        print(f"  Run {run_id}: {', '.join(parts)}")

    # CSV
    csv_path = os.path.join(LOG_DIR, "stage1_summary.csv")
    write_csv(all_metrics, csv_path)

    # 一致性检查
    passed = check_consistency(all_metrics)
    print(f"\n{'=' * 60}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
