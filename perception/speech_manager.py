"""语音调度器：队列 + 合并 + 异步播放。"""

import threading
import time
from queue import PriorityQueue

from tts.tts_backend import LocalTTS


# =========================
# 消息结构
# =========================
class SpeechMessage:
    def __init__(self, text, priority, timestamp, source):
        self.text = text
        self.priority = priority
        self.timestamp = timestamp
        self.source = source


# =========================
# SpeechManager
# =========================
class SpeechManager:
    def __init__(self, min_interval=2.0, stable_count=3, tts_backend=None):
        self.min_interval = min_interval
        self.stable_count = stable_count

        self.last_text = ""
        self.last_time = 0.0
        self.history_buffer = []

        self.queue = PriorityQueue()
        self.lock = threading.Lock()
        self.tts = tts_backend or LocalTTS()

        self._play_queue = PriorityQueue()
        self._running = True
        self._player_thread = threading.Thread(target=self._player_loop, name="speech-player", daemon=True)
        self._player_thread.start()
        self._consumer = threading.Thread(target=self._run_consumer, name="speech-consumer", daemon=True)
        self._consumer.start()

        # --- speech_lock：防重叠 + WARNING可打断 + VLM原子播放 ---
        self.speech_lock = {
            "active": False,
            "owner": None,
            "can_interrupt": True,
        }
        self._play_lock = threading.Lock()  # protects speech_lock dict

    # =========================
    # 后台消费者
    # =========================
    # THREAD: daemon
    def _run_consumer(self):
        while self._running:
            try:
                time.sleep(0.5)
                self._drain()
            except Exception:
                import traceback
                traceback.print_exc()

    def _drain(self):
        msgs = self._collect_messages()
        if not msgs:
            return
        asst_msgs = [m for m in msgs if m.source == "assistant"]
        nav_msgs = [m for m in msgs if m.source != "assistant"]

        target = asst_msgs if asst_msgs else nav_msgs
        rest = nav_msgs if asst_msgs else []

        merged_text = self._merge_messages(target)
        interrupt = any(m.priority > 0 for m in target)
        if merged_text:
            self._speak_async(merged_text, interrupt=interrupt)

        if rest:
            for msg in rest:
                self.queue.put((-msg.priority, msg.timestamp, msg))

    def _collect_messages(self, max_batch=5):
        msgs = []
        while not self.queue.empty() and len(msgs) < max_batch:
            try:
                _, _, msg = self.queue.get_nowait()
                msgs.append(msg)
            except Exception:
                break
        return msgs

    def _merge_messages(self, msgs):
        if not msgs:
            return None
        texts = [m.text for m in msgs]
        return "，".join(texts) + "。"

    # =========================
    # 播放线程（唯一消费者）
    # =========================
    def _speak_async(self, text, interrupt):
        print(f"[TRACE][SPEAK_START] text={text[:30]}")
        self._play_queue.put((0 if interrupt else 1, time.time(), (text, interrupt)))

    # THREAD: daemon
    def _player_loop(self):
        while self._running:
            try:
                _, _, (text, interrupt) = self._play_queue.get(timeout=0.5)

                if interrupt:
                    self.tts.stop()

                self.tts.speak(text, interrupt=False)
                print(f"[TRACE][SPEAK_END] text={text[:30]}")
            except Exception:
                continue

    # =========================
    # Phase B: speech_lock — 防重叠 + WARNING打断 + VLM原子
    # THREAD: main only
    # =========================
    def _can_play(self, item):
        with self._play_lock:
            src = item.get("source")
            priority = item.get("priority", 3)
            is_warning = (priority <= 1)

            if not self.speech_lock["active"]:
                return True

            if is_warning:
                print("[LOCK] WARNING interrupt")
                return True

            if not self.speech_lock["can_interrupt"]:
                print("[LOCK BLOCK]", src)
                print("[TRACE] VLM_BLOCKED: reason=lock_active src=", src)
                return False

            return True

    # THREAD: main only
    def try_play(self, item):
        """带锁保护的播放入口。WARNING 可打断，VLM 原子不可中断。"""
        if not self._can_play(item):
            return False

        src = item.get("source")
        is_vlm = (src == "vlm")

        with self._play_lock:
            self.speech_lock["active"] = True
            self.speech_lock["owner"] = src
            self.speech_lock["can_interrupt"] = not is_vlm

        try:
            self._play_lock_acquired(item)
        finally:
            self.speech_lock["active"] = False
            self.speech_lock["owner"] = None
            self.speech_lock["can_interrupt"] = True

        return True

    def _play_lock_acquired(self, item):
        text = item.get("text", "")
        if not text or not text.strip():
            return
        with self.lock:
            self.last_text = text
            self.last_time = time.time()
            self.history_buffer = [text]
            msg = SpeechMessage(text, 1, time.time(), "assistant")
            self.queue.put((-1, msg.timestamp, msg))

    # =========================
    # Controller 接口（签名不变）
    # =========================
    def update(self, text: str, priority: int = 0) -> bool:
        """普通播报入口，带稳定帧防抖和限频。"""
        if not text or not text.strip():
            return False

        with self.lock:
            self.history_buffer.append(text)
            if len(self.history_buffer) > self.stable_count:
                self.history_buffer.pop(0)

            if len(set(self.history_buffer)) != 1:
                return False

            stable_text = self.history_buffer[0]
            now = time.time()

            if stable_text == self.last_text:
                return False

            if priority == 0 and (now - self.last_time < self.min_interval):
                return False

            msg = SpeechMessage(stable_text, priority, now, "navigation")
            self.queue.put((-priority, now, msg))

            self.last_text = stable_text
            self.last_time = now
            return True

    def speak_now(self, text: str, priority: int = 1, interrupt: bool = True) -> bool:
        """立即播报入口。"""
        if not text or not text.strip():
            return False

        with self.lock:
            if text == self.last_text:
                print(f"[TRACE][SPEAK_DROP] reason=duplicate_text text={text[:30]}")
                return False
            msg = SpeechMessage(text, priority, time.time(), "assistant")
            self.queue.put((-priority, msg.timestamp, msg))

            self.last_text = text
            self.last_time = time.time()
            self.history_buffer = [text]
            return True

    def is_speaking(self):
        return not self._play_queue.empty()

    def stop(self):
        """中断当前播报，并清空待播队列。"""
        self.tts.stop()
        while not self._play_queue.empty():
            try:
                self._play_queue.get_nowait()
            except Exception:
                break

    def reset(self):
        """清空去重和稳定态缓存。"""
        with self.lock:
            self.history_buffer.clear()
            self.last_text = ""
            self.last_time = 0.0
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Exception:
                break
        while not self._play_queue.empty():
            try:
                self._play_queue.get_nowait()
            except Exception:
                break

    def shutdown(self):
        """关闭后台消费者线程。"""
        self._running = False
