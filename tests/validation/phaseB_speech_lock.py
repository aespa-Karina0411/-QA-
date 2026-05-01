"""Phase B: speech_lock 行为验证"""

from .result_schema import ValidationResult


def validate_speech_lock(log):

    result = ValidationResult("PHASE_B_SPEECH_LOCK")

    active = False
    current_owner = None

    overlap = 0
    vlm_interrupted = 0
    warning_interrupt_ok = False

    for event in log:
        etype = event.get("event")
        src = event.get("source", "?")
        pr = event.get("priority", 3)

        is_warning = (pr <= 1)
        is_vlm = (src == "vlm")

        if etype == "play_start":

            if active:
                # WARNING 打断是合法行为，不计为重叠
                if not is_warning:
                    overlap += 1

            if current_owner == "vlm" and not is_warning:
                vlm_interrupted += 1

            if current_owner == "vlm" and is_warning:
                warning_interrupt_ok = True

            active = True
            current_owner = src

        elif etype == "play_end":
            active = False
            current_owner = None

    result.add_metric("overlap", overlap)
    result.add_metric("vlm_interrupted", vlm_interrupted)
    result.add_metric("warning_interrupt_ok", warning_interrupt_ok)

    if overlap > 0:
        result.fail(f"Speech overlap detected: {overlap}")

    if vlm_interrupted > 0:
        result.fail(f"VLM interrupted: {vlm_interrupted}")

    return result
