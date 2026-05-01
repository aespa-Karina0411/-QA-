"""Phase A: Path Integrity — 控制类指令 100% 不进入 VLM 调用链

验证方法:
  - 使用真实 Controller（不 mock 主逻辑）
  - monkeypatch vlm_manager.ask_async 计数
  - 捕获 stdout 检查 [VLM_REQUEST]
  - 断言输出文本不含"正在查看"
"""

import sys
import os
import time
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.controller import Controller
from core.intent_parser import IntentParser, IntentType
from perception.speech_manager import SpeechManager
from perception.decision_utils import DecisionMaker
from vlm.vlm_manager import VLMManager

from .result_schema import ValidationResult


class _MockTTS:
    """无操作 TTS：speak/stop 均为空实现，避免实际语音输出。"""
    def speak(self, text, interrupt=False):
        pass
    def stop(self):
        pass


def validate_path_integrity():
    """Phase A 路径完整性验证。

    四个断言组：
      1. MUTE_NAVIGATION 命令不触发 VLM
      2. RESUME_NAVIGATION 命令不触发 VLM
      3. 边界词"停止播报"识别准确且不触发 VLM
      4. 普通问答正常触发 VLM（回归保证）
    """
    result = ValidationResult("PHASE_A_PATH_INTEGRITY")

    # ── 构建 VLMManager + monkeypatch ask_async 计数器 ──────────────
    vlm_mgr = VLMManager()
    vlm_call_counter = {"count": 0}

    def counting_ask_async(*args, **kwargs):
        vlm_call_counter["count"] += 1
        # 不调用原始 ask_async：我们只需要验证"是否被调用"
        # 真正的 VLM 请求不应在验证中产生副作用

    vlm_mgr.ask_async = counting_ask_async

    # ── 构建真实 Controller（不 mock 主逻辑）────────────────────────
    try:
        ctrl = Controller(
            speech_manager=SpeechManager(
                min_interval=1.0, stable_count=1, tts_backend=_MockTTS()
            ),
            intent_parser=IntentParser(),
            decision_maker=DecisionMaker(),
            vlm_manager=vlm_mgr,
        )
    except Exception as e:
        result.fail(f"Controller init failed: {e}")
        return result

    # 跳过启动阶段 + 注入虚拟图像（GENERAL_QA 需要）
    ctrl._play_startup_message()
    ctrl.context["current_image"] = "data:image/jpeg;base64,/9j/4AAQ=="

    # ── stdout 捕获 ──────────────────────────────────────────────────
    captured = io.StringIO()
    old_stdout = sys.stdout

    try:
        sys.stdout = captured

        # ═══════════════════════════════════════════════════════════════
        # Test 1: MUTE_NAVIGATION — "安静"
        # ═══════════════════════════════════════════════════════════════
        vlm_call_counter["count"] = 0
        captured.truncate(0)
        captured.seek(0)

        resp = ctrl.handle_event({
            "type": "user_input",
            "data": {"text": "安静"},
            "timestamp": time.time(),
        })
        logs = captured.getvalue()

        if vlm_call_counter["count"] != 0:
            result.fail(
                f"MUTE '安静': vlm_manager.ask_async called "
                f"{vlm_call_counter['count']} time(s), expected 0"
            )
        if "[VLM_REQUEST]" in logs:
            result.fail("MUTE '安静': [VLM_REQUEST] appeared in logs")
        resp_text = resp.get("response", "")
        if "正在查看" in resp_text:
            result.fail(f"MUTE '安静': '正在查看' found in response: {resp_text}")
        if resp_text != "好的，已为您开启静音。":
            result.fail(f"MUTE '安静': unexpected response: {resp_text}")

        # ═══════════════════════════════════════════════════════════════
        # Test 2: RESUME_NAVIGATION — "恢复导航"
        # ═══════════════════════════════════════════════════════════════
        vlm_call_counter["count"] = 0
        captured.truncate(0)
        captured.seek(0)

        resp = ctrl.handle_event({
            "type": "user_input",
            "data": {"text": "恢复导航"},
            "timestamp": time.time(),
        })
        logs = captured.getvalue()

        if vlm_call_counter["count"] != 0:
            result.fail(
                f"RESUME '恢复导航': vlm_manager.ask_async called "
                f"{vlm_call_counter['count']} time(s), expected 0"
            )
        if "[VLM_REQUEST]" in logs:
            result.fail("RESUME '恢复导航': [VLM_REQUEST] appeared in logs")
        resp_text = resp.get("response", "")
        if "正在查看" in resp_text:
            result.fail(f"RESUME '恢复导航': '正在查看' found in response: {resp_text}")
        if resp_text != "好的，已恢复导航播报。":
            result.fail(f"RESUME '恢复导航': unexpected response: {resp_text}")

        # ═══════════════════════════════════════════════════════════════
        # Test 3: 边界词 — "停止播报"（修复后必须识别为 MUTE_NAVIGATION）
        # ═══════════════════════════════════════════════════════════════
        vlm_call_counter["count"] = 0
        captured.truncate(0)
        captured.seek(0)

        resp = ctrl.handle_event({
            "type": "user_input",
            "data": {"text": "停止播报"},
            "timestamp": time.time(),
        })
        logs = captured.getvalue()

        if vlm_call_counter["count"] != 0:
            result.fail(
                f"MUTE '停止播报': vlm_manager.ask_async called "
                f"{vlm_call_counter['count']} time(s), expected 0"
            )
        if "[VLM_REQUEST]" in logs:
            result.fail("MUTE '停止播报': [VLM_REQUEST] appeared in logs")
        resp_text = resp.get("response", "")
        if "正在查看" in resp_text:
            result.fail(f"MUTE '停止播报': '正在查看' found in response: {resp_text}")
        if resp_text != "好的，已为您开启静音。":
            result.fail(
                f"MUTE '停止播报': not recognized as mute_navigation. "
                f"Got: {resp_text}"
            )

        # ═══════════════════════════════════════════════════════════════
        # Test 4: 普通 VLM 问答 — "他戴眼镜吗？"（回归保证）
        # ═══════════════════════════════════════════════════════════════
        vlm_call_counter["count"] = 0
        captured.truncate(0)
        captured.seek(0)

        resp = ctrl.handle_event({
            "type": "user_input",
            "data": {"text": "他戴眼镜吗？"},
            "timestamp": time.time(),
        })
        logs = captured.getvalue()

        if vlm_call_counter["count"] == 0:
            result.fail(
                "VLM '他戴眼镜吗？': vlm_manager.ask_async was NOT called"
            )
        if "[VLM_REQUEST]" not in logs:
            result.fail("VLM '他戴眼镜吗？': [VLM_REQUEST] NOT found in logs")
        resp_text = resp.get("response", "")
        if "正在查看" not in resp_text:
            result.fail(
                f"VLM '他戴眼镜吗？': missing '正在查看' in response: {resp_text}"
            )

        # ── 汇总指标 ──────────────────────────────────────────────────
        result.add_metric("vlm_trigger_on_control", 0)
        result.add_metric("vlm_ok_on_query", vlm_call_counter["count"] > 0)
        result.add_metric("tests_run", 4)

    except Exception as e:
        import traceback
        result.fail(f"Test exception: {e}\n{traceback.format_exc()}")
    finally:
        sys.stdout = old_stdout

    return result
