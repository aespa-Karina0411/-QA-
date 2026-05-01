"""Phase A: USER_FOCUS 行为分析器 — 检测非用户语义输出污染"""

from .result_schema import ValidationResult


def validate_user_focus(events):
    result = ValidationResult("USER_FOCUS")

    user_time = None
    vlm_play_time = None
    env_leak = 0
    vlm_played = False
    invalid_output = 0

    for event in events:
        kind = event.get("event")

        if kind == "user_input":
            user_time = event["time"]

        elif kind == "played" and event.get("source") in ("vlm", "user_direct"):
            vlm_play_time = event["time"]
            vlm_played = True

        elif kind == "blocked":
            pass

        elif kind == "played":
            src = event.get("source", "?")
            pr = event.get("priority", 3)
            t = event.get("time", 0)

            # ---- ENV leak: decision + priority=3 在用户查询后播放 ----
            if src == "decision" and pr == 3:
                if user_time is not None and t > user_time:
                    if not vlm_play_time or t < vlm_play_time:
                        env_leak += 1
                        result.fail(
                            f"ENV leak at t={t:.1f}s: {event.get('text','')[:30]}"
                        )

            # ---- 非用户语义输出污染：任何非 VLM/WARNING 的 played 事件 ----
            if user_time and not vlm_play_time:
                if t > user_time:
                    is_vlm = src in ("vlm", "user_direct")
                    is_warning = pr <= 1 and src == "decision"

                    if not (is_vlm or is_warning):
                        invalid_output += 1
                        result.fail(
                            f"Invalid output at t={t:.1f}s: src={src} pr={pr} text={event.get('text','')[:30]}"
                        )

    result.add_metric("env_leak_count", env_leak)
    result.add_metric("invalid_output", invalid_output)
    result.add_metric("vlm_played", vlm_played)
    result.add_metric("user_queries", sum(1 for e in events if e.get("event") == "user_input"))

    if env_leak > 0:
        result.fail(f"ENV leak during USER_FOCUS: {env_leak} occurrences")

    if invalid_output > 0:
        result.fail(f"Invalid output during USER_FOCUS: {invalid_output} occurrences")

    if not vlm_played:
        result.fail("VLM was never played after user query")

    return result
