"""Scenario-driven scheduler experiment generator."""

from __future__ import annotations

import json
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.global_config import CONFIG
from perception import speech_arbitrator as speech_arbitrator_module
from perception.speech_arbitrator import SpeechArbitrator
from observe.trace_logger import TraceLogger

random.seed(42)

REAL_TIME = time.time
REAL_SLEEP = time.sleep
ACTIVE_RUNNER: "SimulationRunner | None" = None

PLAY_DURATION = float(CONFIG.get("speech.play_duration", 2.5))
USER_FOCUS_TIMEOUT = float(CONFIG.get("user_focus.timeout", 5.0))

ENV_TEXTS = [
    "前方有行人",
    "右侧有车辆",
    "左侧有自行车",
    "前方道路较空",
    "右侧有公交车",
    "左前方有台阶",
]

WARNING_TEXTS = [
    "前方存在碰撞风险",
    "右侧车辆靠近",
    "左侧有快速接近目标",
    "前方障碍物距离很近",
]

VLM_TEXTS = [
    "前面有什么？",
    "右边有什么？",
    "左边那个是什么？",
    "有没有危险？",
    "路口怎么过？",
    "前方还有多远？",
    "有没有障碍物？",
    "红绿灯是什么颜色？",
]

USER_TEXTS = [
    "帮我看看前面有什么",
    "右边安全吗",
    "能描述一下环境吗",
    "有没有障碍物",
]


class VirtualClock:
    def __init__(self, start_ts: float = 1_800_000_000.0):
        self.current = start_ts

    def time(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        seconds = max(float(seconds), 0.0)
        if ACTIVE_RUNNER is None:
            self.current += seconds
            return
        ACTIVE_RUNNER.advance(seconds)


class SimulationRunner:
    def __init__(self, output_path: Path):
        self.output_path = output_path
        self.compat_output_path = ROOT / "run_log.json"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.output_path.exists():
            self.output_path.unlink()

        self.clock = VirtualClock()
        self._patched_time = False

        self.trace_logger = TraceLogger(str(self.output_path))
        speech_arbitrator_module._trace_logger = self.trace_logger
        self.arbitrator = SpeechArbitrator()
        self.arbitrator.trace = self.trace_logger

        self.playing_until: float | None = None
        self.current_scene: str | None = None
        self.trace_seq = 0
        self.submitted_items: list[dict[str, Any]] = []
        self.per_scene_counts: Counter[str] = Counter()
        self.context: dict[str, Any] = {
            "system": {
                "user_focus": {
                    "active": False,
                    "enter_ts": 0.0,
                    "timeout": USER_FOCUS_TIMEOUT,
                }
            }
        }

    def patch_time(self) -> None:
        global ACTIVE_RUNNER
        if self._patched_time:
            return
        time.time = self.clock.time
        time.sleep = self.clock.sleep
        ACTIVE_RUNNER = self
        self._patched_time = True

    def restore_time(self) -> None:
        global ACTIVE_RUNNER
        if not self._patched_time:
            return
        time.time = REAL_TIME
        time.sleep = REAL_SLEEP
        ACTIVE_RUNNER = None
        self._patched_time = False

    def run(self, scenarios: list[Callable[["SimulationRunner"], None]]) -> None:
        self.patch_time()
        try:
            for scenario_fn in scenarios:
                self.current_scene = scenario_fn.__name__
                self.trace_logger.log("SCENARIO_START", scenario=self.current_scene)
                scenario_fn(self)
                self.trace_logger.log("SCENARIO_END", scenario=self.current_scene)

            self.force_drain_idle()
            self.write_compat_log()
            self.validate()
            self.print_summary()
        finally:
            self.restore_time()

    def advance(self, seconds: float) -> None:
        target = self.clock.current + seconds
        while True:
            self.update_user_focus()
            if self.playing_until is not None and self.playing_until <= target:
                self.clock.current = self.playing_until
                self.playing_until = None
                self.update_user_focus()
                self.try_start_playback()
                continue

            self.clock.current = target
            self.update_user_focus()
            if self.playing_until is None:
                self.try_start_playback()
            break

    def force_drain_idle(self) -> None:
        while True:
            if self.playing_until is None:
                started = self.try_start_playback()
                if not started:
                    break
            next_finish = self.playing_until
            if next_finish is None:
                break
            self.clock.current = next_finish
            self.playing_until = None

    def update_user_focus(self) -> None:
        focus = self.context["system"]["user_focus"]
        if focus.get("active") and self.clock.current - focus.get("enter_ts", 0.0) > focus.get("timeout", USER_FOCUS_TIMEOUT):
            focus["active"] = False
            self.trace_logger.log("USER_FOCUS_EXIT", scenario=self.current_scene)

    def start_user_focus(self, text: str) -> None:
        focus = self.context["system"]["user_focus"]
        focus["active"] = True
        focus["enter_ts"] = self.clock.current
        focus["timeout"] = USER_FOCUS_TIMEOUT
        self.arbitrator.last_user_query_time = self.clock.current
        self.trace_logger.log(
            "user_input",
            scenario=self.current_scene,
            text=text,
        )
        self.trace_logger.log(
            "USER_FOCUS_ENTER",
            scenario=self.current_scene,
            text=text,
        )

    def submit_env(self, *, is_warning: bool = False, text: str | None = None) -> None:
        priority = 1 if is_warning else 3
        source = "decision"
        default_text = random.choice(WARNING_TEXTS if is_warning else ENV_TEXTS)
        self.submit_item(
            source=source,
            priority=priority,
            text=text or default_text,
        )
        if is_warning:
            self.arbitrator.mark_decision()

    def submit_vlm(
        self,
        *,
        text: str | None = None,
        force_play: bool = False,
        user_focus: bool = False,
    ) -> None:
        self.submit_item(
            source="vlm",
            priority=2,
            text=text or random.choice(VLM_TEXTS),
            force_play=force_play,
            user_focus=user_focus,
        )

    def submit_item(
        self,
        *,
        source: str,
        priority: int,
        text: str,
        force_play: bool = False,
        user_focus: bool = False,
    ) -> None:
        scenario = self.current_scene or "unknown"
        self.trace_seq += 1
        trace_id = f"{scenario[:2]}-{self.trace_seq:04d}"
        item = {
            "trace_id": trace_id,
            "source": source,
            "priority": priority,
            "text": text,
            "time": time.time(),
            "scenario": scenario,
            "user_focus": user_focus,
            "queued": False,
            "played": False,
        }
        if force_play:
            item["force_play"] = True

        self.submitted_items.append(item)
        self.per_scene_counts[scenario] += 1

        self.trace_logger.log(
            "SUBMIT",
            id=trace_id,
            source=source,
            priority=priority,
            scenario=scenario,
            user_focus=user_focus,
            force_play=force_play,
            text=text,
        )

        self.arbitrator.submit(item, context=self.context)
        if priority <= 1:
            self.arbitrator.mark_decision()
        self.try_start_playback()

    def try_start_playback(self) -> bool:
        if self.playing_until is not None:
            return False

        item = self.arbitrator.select_next()
        if item is None:
            return False

        play_time = time.time()
        item["played"] = True
        item["_play_time"] = play_time
        if item.get("source") == "vlm":
            item["vlm_played_time"] = play_time
            self.arbitrator.mark_vlm_played()

        self.trace_logger.log(
            "PLAY",
            id=item.get("trace_id"),
            source=item.get("source"),
            priority=item.get("priority", 3),
            scenario=item.get("scenario"),
            user_focus=bool(item.get("user_focus", False)),
            force_play=bool(item.get("force_play", False)),
            aging_boost=bool(item.get("aging_boost", False)),
        )

        self.playing_until = play_time + PLAY_DURATION
        return True

    def validate(self) -> None:
        records = []
        with self.output_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                text = raw.strip()
                if not text:
                    continue
                records.append(json.loads(text))

        event_count = len(records)
        event_types = Counter(record.get("event") for record in records)
        aging_boost_count = sum(1 for record in records if record.get("aging_boost"))
        user_focus_count = sum(1 for record in records if record.get("event") in {"USER_FOCUS_ENTER", "user_input"})

        if event_count <= 300:
            raise RuntimeError(f"event_count too low: {event_count}")
        for required in ("VLM_SCORE_SELECT", "DROP_CANDIDATE", "PLAY"):
            if event_types.get(required, 0) <= 0:
                raise RuntimeError(f"required event missing: {required}")
        if user_focus_count <= 0:
            raise RuntimeError("USER_FOCUS was not triggered")
        if aging_boost_count <= 0:
            raise RuntimeError("Aging Boost was not triggered")

    def print_summary(self) -> None:
        records = []
        with self.output_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                text = raw.strip()
                if text:
                    records.append(json.loads(text))

        event_types = Counter(record.get("event") for record in records)
        print(f"[SIMULATE] wrote {len(records)} events -> {self.output_path}")
        print(f"[SIMULATE] compat log -> {self.compat_output_path}")
        print(f"[SIMULATE] input items by scenario: {dict(self.per_scene_counts)}")
        print(f"[SIMULATE] event types: {dict(sorted(event_types.items()))}")

    def write_compat_log(self) -> None:
        serializable: list[dict[str, Any]] = []
        for item in self.submitted_items:
            row = {
                key: value
                for key, value in item.items()
                if key not in {"_throttled_once"}
            }
            serializable.append(row)

        with self.compat_output_path.open("w", encoding="utf-8") as handle:
            json.dump(serializable, handle, ensure_ascii=False, indent=2)


def warmup(run: SimulationRunner) -> None:
    for _ in range(10):
        run.submit_env()
        time.sleep(1.0)


def normal(run: SimulationRunner) -> None:
    run.start_user_focus(random.choice(USER_TEXTS))
    time.sleep(1.0)
    run.submit_vlm(text="我来先回答一次用户问题", force_play=True, user_focus=True)

    duration = 60.0
    env_times = schedule_times(duration, 1.5)
    vlm_times = schedule_times(duration, 4.0)
    all_times = sorted({*env_times, *vlm_times})
    current = 0.0

    for when in all_times:
        time.sleep(when - current)
        current = when
        if when in env_times:
            run.submit_env(is_warning=random.random() < 0.10)
        if when in vlm_times:
            run.submit_vlm()

    time.sleep(max(duration - current, 0.0))


def user_focus(run: SimulationRunner) -> None:
    for index in range(3):
        run.start_user_focus(USER_TEXTS[index % len(USER_TEXTS)])
        time.sleep(0.3)
        run.submit_env(text=f"USER_FOCUS 干扰 ENV #{index + 1}")
        time.sleep(0.7)
        run.submit_vlm(
            text=f"USER_FOCUS 响应 #{index + 1}",
            force_play=True,
            user_focus=True,
        )
        time.sleep(4.0)


def stress(run: SimulationRunner) -> None:
    duration = 60.0
    env_times = schedule_times(duration, 0.3)
    vlm_times = schedule_times(duration, 1.0)
    warning_times = schedule_times(duration, 2.0)
    all_times = sorted({*env_times, *vlm_times, *warning_times})
    current = 0.0

    for when in all_times:
        time.sleep(when - current)
        current = when
        if when in env_times:
            run.submit_env()
        if when in warning_times:
            run.submit_env(is_warning=True)
        if when in vlm_times:
            run.submit_vlm()

    time.sleep(max(duration - current, 0.0))


def burst(run: SimulationRunner) -> None:
    for index in range(8):
        run.submit_vlm(text=f"突发 VLM 请求 #{index + 1}")
        if index < 7:
            time.sleep(0.5)


def starvation_test(run: SimulationRunner) -> None:
    duration = 10.0
    warning_times = schedule_times(duration, 0.5)
    vlm_times = schedule_times(duration, 2.0)
    all_times = sorted({*warning_times, *vlm_times})
    current = 0.0

    for when in all_times:
        time.sleep(when - current)
        current = when
        if when in warning_times:
            run.submit_env(is_warning=True, text="饥饿测试 WARNING")
        if when in vlm_times:
            run.submit_vlm(text="饥饿测试 VLM")

    time.sleep(max(duration - current, 0.0))


def recovery(run: SimulationRunner) -> None:
    for _ in range(15):
        run.submit_env(text="恢复阶段 ENV")
        time.sleep(2.0)


SCENARIOS = [
    warmup,
    normal,
    user_focus,
    stress,
    burst,
    starvation_test,
    recovery,
]


def schedule_times(duration: float, interval: float) -> set[float]:
    values: set[float] = set()
    index = 0
    while True:
        point = round(index * interval, 6)
        if point >= duration:
            break
        values.add(point)
        index += 1
    return values


def main() -> None:
    output_path = ROOT / "logs" / "full_run.jsonl"
    runner = SimulationRunner(output_path)
    runner.run(SCENARIOS)


if __name__ == "__main__":
    main()
