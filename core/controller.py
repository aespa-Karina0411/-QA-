# controller.py
"""系统中央控制器：基于事件驱动的状态管理与指令分发中心。"""

from collections import deque
import time
import uuid
from typing import Any, Dict

from assistant.local_scene_qa import local_scene_qa
from expression.expression_engine import ExpressionEngine
from perception.speech_manager import SpeechManager
from perception.speech_arbitrator import SpeechArbitrator
from perception.output_policy import OutputPolicy
from .response_router import ResponseRouter
from vlm.vlm_intent_parser import build_prompt, parse_vlm_intent
from vlm.vlm_manager import VLMManager

from .EnvironmentDescriber import EnvironmentDescription
from .intent_parser import IntentParser, IntentType
from core.global_config import CONFIG


class Controller:
    MODE_NAVIGATION = "navigation"
    MODE_ASSISTANT = "assistant"

    EVENT_NAVIGATION = "navigation"
    EVENT_USER_INPUT = "user_input"
    EVENT_COMMAND = "command"

    MAX_DIALOG_HISTORY = 6

    def __init__(
        self,
        speech_manager=None,
        assistant_handler=None,
        env_describer=None,
        assistant_idle_timeout=8.0,
        intent_parser=None,
        decision_maker=None,
        spatial_parser=None,
        vlm_manager=None,
        enable_logging=False,
    ):
        self.speech = speech_manager or SpeechManager(min_interval=2.0, stable_count=3)
        self.assistant_handler = assistant_handler
        self.env_describer = env_describer or EnvironmentDescription()
        self.intent_parser = intent_parser or IntentParser()
        self.decision_maker = decision_maker
        self.spatial_parser = spatial_parser
        self.vlm_manager = vlm_manager or VLMManager()
        self.expression = ExpressionEngine()
        self.arbitrator = SpeechArbitrator()
        self.vlm_manager.trace = self.arbitrator.trace
        self.enable_logging = enable_logging
        self.enable_output_policy = True          # 输出策略开关（可回滚）
        self.output_policy = OutputPolicy()
        self.response_router = ResponseRouter()
        self.logs = deque(maxlen=500)

        self.mode = self.MODE_NAVIGATION
        self.navigation_muted = False
        self.assistant_idle_timeout = assistant_idle_timeout
        self.last_user_input_time = 0.0

        # PHASE 1: 对话优先级窗口 — 用户提问后 3s 内屏蔽环境播报
        self.user_query_active_until = 0.0

        # PHASE 1: 语音节流 — 防止短时间重复播报
        self._last_spoken_text = ""
        self._last_spoken_time = 0.0

        # Startup: 启动阶段标志 — 禁止导航干扰 + 启动语句不受策略限制
        self.is_startup_phase = True
        self.startup_played = False

        # Cold Start: 启动后首次环境播报（仅一次，2s 窗口）
        self.cold_start_active = True
        self.cold_start_env_played = False
        self.cold_start_start_time = time.time()
        self.cold_start_duration = 2.0

        # 配置驱动的限频控制
        self._last_decision_time = 0.0
        self._decision_interval = CONFIG.get("system.decision_interval", 0.5)

        self.context = {}
        self.context.setdefault("scene", {
            "objects": [],
            "last_update": None,
            "version": 0,
            "state": "unstable",
        })
        self.context.setdefault("dialog", {
            "history": [],
            "last_time": None,
        })
        self.context.setdefault("system", {
            "mode": self.mode,
            "navigation_muted": self.navigation_muted,
            "event_log": deque(maxlen=10),
            "user_focus": {
                "active": False,
                "enter_ts": 0.0,
                "timeout": 5.0,
            },
        })

        self._scene_buffer = []
        self._scene_stable_count = 0
        self._scene_last_stable = None
        self.scene_version = 0

    def handle_event(self, event: Dict[str, Any]):
        """统一事件入口，支持处理 ASR 直接生成的扁平事件。"""
        self._poll_vlm_results()
        self._update_user_focus()

        event_type = event.get("type")
        data = event.get("data", event)
        timestamp = event.get("timestamp") or time.time()

        if event_type == self.EVENT_NAVIGATION:
            return self._on_navigation_event(data, timestamp)
        if event_type == self.EVENT_USER_INPUT:
            return self._on_user_input_event(data, timestamp)
        if event_type == self.EVENT_COMMAND:
            return self._on_command_event(data)

        print(f"[Warning] Unknown event type: {event_type}")
        return None

    def _on_navigation_event(self, data: Dict[str, Any], timestamp: float):
        """处理来自感知层的导航更新事件。"""
        if self.is_startup_phase:
            return {"mode": self.mode, "speech": {"spoken": False, "reason": "startup_phase"}}

        # USER_FOCUS 期间冻结 scene 更新，防止 version 漂移导致 VLM 结果被丢弃
        if self.context["system"]["user_focus"]["active"]:
            return {"mode": self.mode, "speech": {"spoken": False, "reason": "user_focus_frozen"}}

        # 配置限频
        if timestamp - self._last_decision_time < self._decision_interval:
            return {"mode": self.mode, "speech": {"spoken": False}}
        self._last_decision_time = timestamp

        if not CONFIG.get("system.enable_env", True):
            return {"mode": self.mode, "speech": {"spoken": False, "reason": "env_disabled"}}
        raw_objects = data.get("objects", [])
        frame_shape = data.get("frame_shape", (0, 0))
        env_data = self.spatial_parser.parse(raw_objects, frame_shape)
        new_scene = env_data.get("objects", [])
        if self._is_scene_stable(new_scene):
            self.context["scene"]["objects"] = env_data
            self.context["scene"]["last_update"] = time.time()
            self.scene_version += 1
            self.context["scene"]["version"] = self.scene_version
            self.context["scene"]["state"] = "stable"
            self.context["dialog"]["history"].clear()
            self.context["dialog"]["last_time"] = None
            print("[Context] Scene changed → reset dialog")
            decision = self.decision_maker.get_decision(self.context["scene"]["objects"])

            # Cold Start: 首次环境播报绕过所有限制
            now = time.time()
            if self.cold_start_active and not self.cold_start_env_played:
                if now - self.cold_start_start_time < self.cold_start_duration:
                    decision["bypass_throttle"] = True
                    decision["bypass_policy"] = True
                    decision["is_cold_start_env"] = True
                    decision["force_play"] = True
                    self.cold_start_env_played = True
                    self.cold_start_active = False
                    print("[COLD_START] First env broadcast triggered")

            speech_result = self._route_speech_decision(decision, timestamp)
            self._check_mode_timeout(timestamp)
            return {"mode": self.mode, "speech": speech_result}
        else:
            self.context["scene"]["state"] = "unstable"
            return {"mode": self.mode, "speech": {"spoken": False}}

    def _on_vlm_result(self, answer: str, version: int = 0, is_fallback: bool = False):
        """统一接收 VLM 输出结果（唯一语音出口）。VLM 失败不回退到 Decision。"""
        print("[TRACE] VLM_RESULT_CALLBACK")
        if not answer:
            print("[TRACE] VLM_DROP: empty_answer")
            return
        if version != self.context["scene"].get("version"):
            print("[TRACE] VLM_DROP: version_mismatch got=", version, "current=", self.context["scene"].get("version"))
            return

        if is_fallback:
            print("[VLM_FALLBACK] VLM returned null/exception, using fallback text")

        trace_id = uuid.uuid4().hex[:6]
        print("[TRACE][VLM_READY]", trace_id)
        print("[TRACE][SUBMIT]", f"id={trace_id} source=vlm priority=2 text={answer[:30]}")
        print("[TRACE] VLM_SUBMIT_TO_ARBITRATOR id=", trace_id)

        self.arbitrator.submit({
            "text": answer,
            "source": "vlm",
            "priority": 2,
            "time": time.time(),
            "trace_id": trace_id,
            "force_play": True,
            "user_focus": self.context["system"]["user_focus"]["active"],
        }, context=self.context)

    def _poll_vlm_results(self):
        result = self.vlm_manager.poll_result()
        if result:
            self._on_vlm_result(
                result["text"],
                result.get("version", 0),
                result.get("is_fallback", False),
            )

    def _drain_arbitrator(self):
        item = self.arbitrator.select_next()
        if item is None:
            return

        if item.get("force_play"):
            print(f"[TRACE][FORCE_PLAY_EXECUTED] id={item.get('trace_id','?')}")

        if item.get("source") == "vlm":
            print("[TRACE] VLM_SELECTED_FOR_PLAY id=", item.get("trace_id", "?"))

        try:
            success = self.speech.try_play(item)
        except Exception as e:
            success = False
            print(f"[TRACE][PLAY_FAIL] id={item.get('trace_id','?')} reason=exception:{e}")
            self.arbitrator.trace.log("PLAY_FAIL",
                id=item.get("trace_id"),
                reason=f"exception:{e}")

        if not success:
            print(f"[TRACE][PLAY_FAIL] id={item.get('trace_id','?')} reason=speech_busy")
            self.arbitrator.trace.log("PLAY_FAIL",
                id=item.get("trace_id"),
                reason="speech_busy")
            return

        self.arbitrator.trace.log("PLAY",
            id=item.get("trace_id"),
            source=item.get("source"),
            priority=item.get("priority", 3))

        if item.get("source") == "vlm":
            print("[TRACE] VLM_PLAY id=", item.get("trace_id", "?"))
            self.arbitrator.mark_vlm_played()

    def _play_startup_message(self):
        """启动专用播报：不受 throttle/OutputPolicy/queue 限制"""
        if self.startup_played:
            return

        text = "系统已启动。按 A 开始语音输入，按 Q 退出。"
        trace_id = uuid.uuid4().hex[:6]
        print("[TRACE][STARTUP_TRIGGER]")
        print("[TRACE][SUBMIT]", f"id={trace_id} source=startup priority=0 text={text[:30]}")

        self.arbitrator.submit({
            "text": text,
            "source": "startup",
            "priority": 0,
            "time": time.time(),
            "trace_id": trace_id,
            "bypass_throttle": True,
        }, context=self.context)

        self.startup_played = True
        self.is_startup_phase = False

    def _on_user_input_event(self, data: Dict[str, Any], timestamp: float):
        """处理来自语音识别（ASR）的用户意图事件。"""
        text = data.get("text", "")
        print("[USER_INPUT]", text)
        print("[TRACE] USER_QUERY received:", text)

        self._enter_user_focus()

        if self._should_reset_dialog(timestamp):
            self.context["dialog"]["history"].clear()

        RESET_KEYWORDS = ["重新开始", "重置", "reset", "清空"]
        if any(k in text.lower() for k in RESET_KEYWORDS):
            self.context["dialog"]["history"] = []
            self.context["dialog"]["last_time"] = None
            trace_id_reset = uuid.uuid4().hex[:6]
            print("[TRACE][SUBMIT]", f"id={trace_id_reset} source=decision priority=1 text=好的，我们重新开始。")
            self.arbitrator.submit({
                "text": "好的，我们重新开始。",
                "source": "decision",
                "priority": 1,
                "time": time.time(),
                "trace_id": trace_id_reset,
            }, context=self.context)
            return {"mode": self.mode, "response": "好的，我们重新开始。", "spoken": True}

        intent_result = data.get("intent_result") or self.intent_parser.parse(text)
        print("[INTENT]", intent_result.intent)

        route = self.response_router.route(intent_result, self.context)

        if isinstance(route, dict):
            trace_id_resp = uuid.uuid4().hex[:6]
            print("[TRACE][SUBMIT]", f"id={trace_id_resp} source=user_direct priority=2 text={route['text'][:30]}")
            self.arbitrator.submit({
                "text": route["text"],
                "source": "user_direct",
                "priority": 2,
                "time": time.time(),
                "trace_id": trace_id_resp,
            }, context=self.context)
            self.user_query_active_until = timestamp + 3.0
            self._enter_assistant_mode(reason="user_interaction")
            self.context["dialog"]["last_time"] = timestamp
            self.context["dialog"]["history"].append({"user": text, "assistant": route["text"]})
            return {"mode": self.mode, "response": route["text"], "spoken": True}

        if route == "VLM":
            intent_result.intent = "general_qa"
        elif route == "FALLBACK":
            if intent_result.intent not in (
                IntentType.MUTE_NAVIGATION,
                IntentType.RESUME_NAVIGATION,
            ):
                intent_result.intent = "general_qa"

        self.last_user_input_time = timestamp
        self.user_query_active_until = timestamp + 3.0
        self._enter_assistant_mode(reason="user_interaction")

        response = ""
        if intent_result.intent == IntentType.MUTE_NAVIGATION:
            self.navigation_muted = True
            response = "好的，已为您开启静音。"
        elif intent_result.intent == IntentType.RESUME_NAVIGATION:
            self.navigation_muted = False
            self._enter_navigation_mode(reason="resume")
            response = "好的，已恢复导航播报。"
        elif intent_result.intent == IntentType.DESCRIBE_ENVIRONMENT:
            response = self.env_describer.generate(self.context)
        elif intent_result.intent == IntentType.SCENE_QA:
            response = local_scene_qa(self.context, intent_result.text)
        elif intent_result.intent == IntentType.GENERAL_QA:
            image = self.context.get("current_image")
            if not CONFIG.get("system.enable_vlm", True):
                response = "VLM 功能已关闭。"
            elif not image:
                response = "当前没有图像可供分析。"
            else:
                vlm_context = build_vlm_context(self.context)
                scene_data = self.context["scene"]["objects"] or {}
                objects_desc = str(scene_data if isinstance(scene_data, list) else scene_data.get("objects", []))
                vlm_intent = parse_vlm_intent(text, self.context)
                if vlm_intent:
                    prompt = build_prompt(vlm_intent, self.context)
                else:
                    prompt = f"当前环境中有：{objects_desc}\n用户问题：{text}"
                current_version = self.context["scene"]["version"]
                print("[VLM_REQUEST]")
                print("[TRACE] VLM_TRIGGER")
                self.vlm_manager.ask_async(image, prompt, vlm_context, version=current_version)
                self.arbitrator.last_user_query_time = time.time()
                response = "正在查看，请稍等"
        else:
            response = self._invoke_assistant_handler(intent_result.text)

        trace_id_resp = uuid.uuid4().hex[:6]
        print("[TRACE][SUBMIT]", f"id={trace_id_resp} source=decision priority=1 text={response[:30]}")
        self.arbitrator.submit({
            "text": response, "source": "decision", "priority": 1,
            "time": time.time(), "trace_id": trace_id_resp,
        }, context=self.context)
        self._log_event("assistant", response, priority=1)
        self.context["dialog"]["last_time"] = timestamp
        self.context["dialog"]["history"].append({"user": text, "assistant": response})
        history = self.context["dialog"]["history"]
        self.context["dialog"]["history"] = history[-self.MAX_DIALOG_HISTORY:]
        return {"mode": self.mode, "response": response, "spoken": True}

    def _on_command_event(self, data: Dict[str, Any]):
        cmd = data.get("cmd", "").lower()
        if cmd == "switch_to_assistant":
            self._enter_assistant_mode(reason="manual_command")
        elif cmd == "switch_to_navigation":
            self._enter_navigation_mode(reason="manual_command")
        return {"mode": self.mode, "status": "command_executed"}

    def _route_speech_decision(self, decision, timestamp):
        if not decision or not decision.get("should_speak"):
            # check for decision-layer suppress reason
            reason = decision.get("suppress_reason") if decision else None
            if reason:
                tid = uuid.uuid4().hex[:6]
                self.arbitrator.trace.log("SUPPRESS", id=tid, stage="decision", reason=reason)
            return {"spoken": False}
        tid = uuid.uuid4().hex[:6]
        if self.navigation_muted:
            return {"spoken": False, "reason": "muted"}
        priority = decision.get("priority", 0)
        if self.mode == self.MODE_ASSISTANT and priority <= 0:
            return {"spoken": False, "reason": "suppressed_by_assistant_mode"}
        now = time.time()
        if now < self.user_query_active_until:
            intent = decision.get("intent", "")
            if intent in ("ENVIRONMENT_DESC", "STATUS_UPDATE"):
                return {"spoken": False, "reason": "suppressed_by_conversation_window"}
        text = self.expression.generate(decision)
        if text == self._last_spoken_text and now - self._last_spoken_time < 2.5:
            return {"spoken": False, "reason": "throttled"}
        self._last_spoken_text = text
        self._last_spoken_time = now
        arb_priority = 1 if decision.get("priority") == 1 else 3
        if self.enable_output_policy:
            scene_objects = self.context.get("scene", {}).get("objects", {})
            if isinstance(scene_objects, dict):
                current_objects = scene_objects.get("objects", [])
            else:
                current_objects = scene_objects if isinstance(scene_objects, list) else []
            allowed, reason = self.output_policy.allow({
                "text": text, "priority": arb_priority, "source": "decision",
                "objects": current_objects,
                "bypass_policy": decision.get("bypass_policy", False),
                "is_cold_start_env": decision.get("is_cold_start_env", False),
            })
            if not allowed:
                self.arbitrator.trace.log("SUPPRESS", id=tid, stage="policy", reason=reason or "output_policy")
                return {"spoken": False, "reason": "suppressed_by_output_policy"}
        print("[TRACE][SUBMIT]", f"id={tid} source=decision priority={arb_priority} text={text[:30]}")
        self.arbitrator.submit({
            "text": text, "source": "decision", "priority": arb_priority,
            "time": time.time(), "trace_id": trace_id,
            "bypass_throttle": decision.get("bypass_throttle", False),
            "is_cold_start_env": decision.get("is_cold_start_env", False),
            "force_play": decision.get("force_play", False),
        }, context=self.context)
        self.arbitrator.mark_decision()
        self._log_event("navigation", text, priority)
        return {"spoken": True}

    def _log_event(self, channel, text, priority):
        self.context["system"]["event_log"].append({
            "channel": channel, "text": text,
            "priority": priority, "timestamp": time.time(),
        })

    def _check_mode_timeout(self, timestamp):
        if self.mode == self.MODE_ASSISTANT and self.last_user_input_time:
            if timestamp - self.last_user_input_time >= self.assistant_idle_timeout:
                self._enter_navigation_mode(reason="idle_timeout")

    def _enter_assistant_mode(self, reason):
        if self.mode != self.MODE_ASSISTANT:
            self.mode = self.MODE_ASSISTANT
            self.speech.stop()

    def _enter_user_focus(self):
        self.context["system"]["user_focus"] = {
            "active": True, "enter_ts": time.time(), "timeout": 5.0,
        }

    def _update_user_focus(self):
        uf = self.context["system"].get("user_focus", {})
        if not uf.get("active"):
            return
        if time.time() - uf["enter_ts"] > uf["timeout"]:
            uf["active"] = False

    def _enter_navigation_mode(self, reason):
        self.mode = self.MODE_NAVIGATION

    def _is_scene_stable(self, current_scene):
        N = 3
        current_set = {(o["class_zh"], o["direction"]) for o in current_scene}
        self._scene_buffer.append(current_set)
        if len(self._scene_buffer) > N:
            self._scene_buffer.pop(0)
        if len(self._scene_buffer) == N and all(s == current_set for s in self._scene_buffer):
            if self._scene_last_stable != current_set:
                self._scene_last_stable = current_set
                return True
        return False

    def _should_reset_dialog(self, timestamp: float) -> bool:
        last = self.context["dialog"].get("last_time")
        if not last:
            return False
        return (timestamp - last) > 10.0

    def save_log(self, path="run_log.json"):
        if not self.enable_logging:
            return
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(self.logs), f, indent=2, ensure_ascii=False, default=str)

    def get_dialog_history(self):
        return self.context["dialog"]["history"]

    def _invoke_assistant_handler(self, text):
        if not self.assistant_handler:
            return "助手模式已就绪。"
        return self.assistant_handler(text=text, context=self.context)


def is_scene_changed(old, new):
    old_list = old.get("objects", []) if isinstance(old, dict) else old
    new_list = new.get("objects", []) if isinstance(new, dict) else new

    old_set = {(o["class_zh"], o["direction"]) for o in old_list}
    new_set = {(o["class_zh"], o["direction"]) for o in new_list}

    return old_set != new_set


def build_vlm_context(context):
    env_data = context["scene"]["objects"] or {}
    objects = env_data if isinstance(env_data, list) else env_data.get("objects", [])
    return {
        "objects": objects,
        "recent_events": context["dialog"]["history"][-3:]
    }
