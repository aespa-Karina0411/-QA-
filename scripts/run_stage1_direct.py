"""
Stage 1 自动化实验控制脚本
==========================
直接驱动 SpeechArbitrator + TraceLogger，无需 GUI / stdin / cv2。

三轮实验：
  low   — 每 5s 一个任务，持续 120s
  mid   — 每 2s 一个任务，持续 120s
  burst — 10 连发 + 10s 静默，重复 3 轮

输出：logs/stage1_auto_low.jsonl / mid.jsonl / burst.jsonl
"""

import os
import shutil
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
    "帮我看看前面有什么",
    "右边安全吗",
    "能描述一下环境吗",
    "有没有障碍物",
    "前面有什么？",
    "左边那个是什么？",
    "有没有危险？",
    "路口怎么过？",
    "红绿灯是什么颜色？",
    "前方还有多远？",
]


class Stage1Runner:
    def __init__(self, experiment_name: str):
        os.makedirs(LOG_DIR, exist_ok=True)
        self.trace_path = os.path.join(LOG_DIR, "trace.jsonl")
        if os.path.exists(self.trace_path):
            os.remove(self.trace_path)

        self.trace = TraceLogger(self.trace_path)
        sa_module._trace_logger = self.trace
        self.arb = SpeechArbitrator()
        self.arb.trace = self.trace

        self.playing_until: float | None = None
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

    # ── 任务注资 ─────────────────────────────────────────────
    def submit(self, text: str, source: str = "user_direct", priority: int = 2):
        tid = f"s1-{uuid.uuid4().hex[:6]}"
        item = {
            "trace_id": tid,
            "source": source,
            "priority": priority,
            "text": text,
            "time": time.time(),
        }
        self.arb.submit(item, context=self.context)
        self.submitted += 1

    # ── 播放模拟 ─────────────────────────────────────────────
    def tick(self) -> bool:
        """尝试播放一条任务。返回 True 表示播放了某个条目。"""
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
        """排空所有队列中的残留任务。"""
        deadline = time.time() + 120
        while time.time() < deadline:
            if not self.tick():
                if self.playing_until is not None:
                    time.sleep(min(self.playing_until - time.time(), 0.1))
                    continue
                if not (self.arb.warning_queue or self.arb.vlm_queue or self.arb.env_queue):
                    break
                time.sleep(0.05)

    def save_copy(self, name: str):
        src = self.trace_path
        dst = os.path.join(LOG_DIR, f"stage1_auto_{name}.jsonl")
        shutil.copy(src, dst)
        return dst


# ── 实验函数 ─────────────────────────────────────────────────

def run_low() -> str:
    r = Stage1Runner("low")
    print("[LOW] 每 5s 一个任务，持续 120s …")
    deadline = time.time() + 120
    next_submit = time.time()
    idx = 0

    while time.time() < deadline:
        r.tick()
        if time.time() >= next_submit:
            text = STAGE1_TEXTS[idx % len(STAGE1_TEXTS)]
            r.submit(text)
            idx += 1
            next_submit = time.time() + 5.0
        time.sleep(0.05)

    r.drain_all()
    path = r.save_copy("low")
    print(f"  [LOW] 完成  submitted={r.submitted}  played={r.played}  →  {path}")
    return path


def run_mid() -> str:
    r = Stage1Runner("mid")
    print("[MID] 每 2s 一个任务，持续 120s …")
    deadline = time.time() + 120
    next_submit = time.time()
    idx = 0

    while time.time() < deadline:
        r.tick()
        if time.time() >= next_submit:
            text = STAGE1_TEXTS[idx % len(STAGE1_TEXTS)]
            r.submit(text, source="vlm", priority=2)
            idx += 1
            next_submit = time.time() + 2.0
        time.sleep(0.05)

    r.drain_all()
    path = r.save_copy("mid")
    print(f"  [MID] 完成  submitted={r.submitted}  played={r.played}  →  {path}")
    return path


def run_burst() -> str:
    r = Stage1Runner("burst")
    print("[BURST] 10 连发 + 10s 静默，重复 3 轮 …")

    for round_n in range(3):
        for i in range(10):
            text = f"burst_r{round_n + 1}_{i + 1}"
            r.submit(text, source="user_direct", priority=2)
            time.sleep(0.1)
            r.tick()

        pause_until = time.time() + 10.0
        while time.time() < pause_until:
            r.tick()
            time.sleep(0.05)

    r.drain_all()
    path = r.save_copy("burst")
    print(f"  [BURST] 完成  submitted={r.submitted}  played={r.played}  →  {path}")
    return path


# ── 自动评估 ─────────────────────────────────────────────────

def evaluate(path: str):
    print(f"\n  ── 评估 {os.path.basename(path)} ──")
    eval_script = os.path.join(ROOT, "analysis", "evaluate_scheduler.py")
    subprocess_result = __import__("subprocess").run(
        [sys.executable, eval_script, path],
        capture_output=False,
        text=False,
    )
    return subprocess_result.returncode


# ── main ─────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Stage 1 自动化实验")
    print("  (low / mid / burst)")
    print("=" * 60)

    t0 = time.time()
    paths = {}

    for name, func in [("low", run_low), ("mid", run_mid), ("burst", run_burst)]:
        print()
        paths[name] = func()

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  全部完成，总耗时 {elapsed:.0f}s")
    for name, p in paths.items():
        size_kb = os.path.getsize(p) / 1024
        print(f"    {os.path.basename(p)}  ({size_kb:.0f} KB)")
    print(f"{'=' * 60}")

    for name, p in paths.items():
        evaluate(p)


if __name__ == "__main__":
    main()
