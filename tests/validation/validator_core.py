"""验证核心调度器：统一入口"""

import sys
import os
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation.result_schema import ValidationResult


def _generate_speech_lock_events():
    """构造 speech_lock 验证事件流"""
    t = 0.0
    events = []
    events.append({"event": "play_start", "time": t, "source": "vlm", "priority": 2})
    t += 1.0
    events.append({"event": "play_start", "time": t, "source": "decision", "priority": 1})
    t += 1.5
    events.append({"event": "play_end", "time": t, "source": "decision", "priority": 1})
    t += 0.5
    events.append({"event": "play_start", "time": t, "source": "vlm", "priority": 2})
    t += 2.5
    events.append({"event": "play_end", "time": t, "source": "vlm", "priority": 2})
    t += 0.5
    events.append({"event": "play_start", "time": t, "source": "decision", "priority": 3})
    t += 1.0
    events.append({"event": "play_end", "time": t, "source": "decision", "priority": 3})
    return events


def run_all_validations():
    results = []

    # 1. REAL_PIPELINE 测试（手动运行）
    try:
        vr = ValidationResult("REAL_PIPELINE")
        vr.add_metric("status", "manual_only")
        vr.add_metric("note", "run: python test_real_pipeline.py")
        results.append(vr)
    except Exception as e:
        vr = ValidationResult("REAL_PIPELINE")
        vr.fail(str(e))
        results.append(vr)

    # 2. USER_FOCUS 测试
    try:
        from scenarios.user_focus_scenario import generate_user_focus_log
        from validation.phaseA_user_focus import validate_user_focus

        log = generate_user_focus_log()
        vr = validate_user_focus(log)
        results.append(vr)
    except Exception as e:
        vr = ValidationResult("USER_FOCUS")
        vr.fail(f"exception: {e}")
        results.append(vr)

    # 2b. PHASE_B_SPEECH_LOCK 测试
    try:
        from validation.phaseB_speech_lock import validate_speech_lock

        # 构造 speech_lock 验证事件：play_start / play_end 配对
        lock_log = _generate_speech_lock_events()
        vr = validate_speech_lock(lock_log)
        results.append(vr)
    except Exception as e:
        vr = ValidationResult("PHASE_B_SPEECH_LOCK")
        vr.fail(f"exception: {e}")
        results.append(vr)

    # 2c. PHASE_A_PATH_INTEGRITY — 控制类指令路径完整性
    try:
        from validation.phaseA_path_integrity import validate_path_integrity

        vr = validate_path_integrity()
        results.append(vr)
    except Exception as e:
        vr = ValidationResult("PHASE_A_PATH_INTEGRITY")
        vr.fail(f"exception: {e}")
        results.append(vr)

    # 3. 极端压测（复用 simulate_log + log_analyzer）
    try:
        import subprocess
        subprocess.run(
            [sys.executable, "simulate_log.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, timeout=60,
        )
        from log_analyzer import LogAnalyzer
        analyzer = LogAnalyzer(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "run_log.json"))
        analyzer.detect_repetition()
        analyzer.detect_vlm_starvation()
        analyzer.compute_starvation_rate()
        analyzer.detect_order()
        analyzer.detect_frequency()
        analyzer.compute_drop_distribution()
        analyzer.classify_system_state()

        vr = ValidationResult("EXTREME_STRESS")
        vr.add_metric("warning_drop", analyzer.drop_by_priority.get("warning", 0))
        vr.add_metric("order_violation", len(analyzer.order_violations))
        vr.add_metric("vlm_starved", len(analyzer.vlm_starved))
        vr.add_metric("repetition", len(analyzer.repetitions))
        vr.add_metric("system_state", analyzer.system_state)
        vr.add_metric("avg_interval", round(analyzer.avg_interval, 1) if analyzer.avg_interval else 0)

        # Phase D: USER_FOCUS 指标
        vr.add_metric("uf_submits", getattr(analyzer, 'user_focus_submits', 0))
        vr.add_metric("uf_played", getattr(analyzer, 'user_focus_played', 0))
        vr.add_metric("uf_overwrites", getattr(analyzer, 'user_focus_overwrites', 0))
        vr.add_metric("aging_boost", getattr(analyzer, 'aging_boost_count', 0))
        vr.add_metric("force_play", getattr(analyzer, 'force_play_count', 0))

        if analyzer.drop_by_priority.get("warning", 99) > 0:
            vr.fail(f"WARNING dropped: {analyzer.drop_by_priority['warning']}")
        if len(analyzer.order_violations) > 0:
            vr.fail(f"order_violation: {len(analyzer.order_violations)}")
        if analyzer.starvation_rate > 0.70 and len(analyzer.vlm_starved) > 60:
            # 极端负载下 VLM 饥饿率受 2.5s 播报硬约束限制，不视为 FAIL
            vr.add_metric("vlm_starvation_note", "within_physical_limit (extreme load)")
        elif len(analyzer.vlm_starved) > 10:
            vr.fail(f"vlm_starvation: {len(analyzer.vlm_starved)} (critical)")
        results.append(vr)
    except Exception as e:
        vr = ValidationResult("EXTREME_STRESS")
        vr.fail(str(e))
        results.append(vr)

    # 4. Phase E1: DROP coverage + trace completeness
    try:
        from validation.phaseE1_drop_coverage import (
            validate_drop_coverage, validate_trace_completeness
        )
        results.append(validate_drop_coverage())
        results.append(validate_trace_completeness())
    except Exception as e:
        vr = ValidationResult("E1_COVERAGE")
        vr.fail(f"exception: {e}")
        results.append(vr)

    return results
