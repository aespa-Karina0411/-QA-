import base64
import os
import queue
import select
import sys
import threading
import time

import cv2
from camera.factory import create_camera_provider
from perception.spatial_adapter import SpatialParserAdapter
from asr.asrmanager import ASRManager
from core.controller import Controller
from core.global_config import CONFIG
from perception.decision_utils import DecisionMaker
from perception.speech_manager import SpeechManager
from tts import tts_local_utils
import perception.yolo_utils as yolo_utils


def encode_frame_as_data_url(frame) -> str | None:
    """Encode an OpenCV frame to a JPEG data URL for VLM consumption."""
    success, buffer = cv2.imencode(".jpg", frame)
    if not success:
        return None

    encoded = base64.b64encode(buffer).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _draw_hud(frame, controller):
    """将队列状态叠加到摄像头画面右下角（只读，零调度影响）"""
    arb = controller.arbitrator
    sm = controller.speech
    ctx = controller.context
    focus = ctx["system"]["user_focus"]
    now = __import__("time").time()

    playing = (sm.speech_lock["owner"] or "idle") if sm.speech_lock["active"] else "idle"
    throttled = "ACTIVE" if now - arb.last_play_time < 1.5 else "idle"
    last_vlm = f"{now - arb.last_vlm_play_time:.1f}s ago" if arb.last_vlm_play_time > 0 else "none"

    lines = [
        f"W:{len(arb.warning_queue)}  V:{len(arb.vlm_queue)}  E:{len(arb.env_queue)}  PLAY:{playing}  FOCUS:{focus.get('active',False)}",
        f"THROTTLE:{throttled}  LAST_VLM:{last_vlm}",
    ]
    h, w = frame.shape[:2]
    for i, line in enumerate(lines):
        y = h - 20 + i * 18
        cv2.putText(frame, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)


class AsyncASRInput:
    """后台采集 ASR，并将识别结果回传为 Event。"""

    def __init__(self, asr_manager: ASRManager):
        self.asr_manager = asr_manager
        self.event_queue = queue.Queue()
        self._worker = None
        self._lock = threading.Lock()

    def start_listening(self) -> bool:
        """启动一次后台 ASR 采集；若已有任务在运行则忽略。"""
        with self._lock:
            if self._worker and self._worker.is_alive():
                return False

            self._worker = threading.Thread(
                target=self._listen_worker,
                name="asr-listener",
                daemon=True,
            )
            self._worker.start()
            return True

    def poll_event(self):
        """非阻塞获取一条已完成的 ASR Event。"""
        try:
            return self.event_queue.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        worker = None
        with self._lock:
            worker = self._worker

        if worker and worker.is_alive():
            worker.join(timeout=0.2)

    def _listen_worker(self):
        try:
            event = self.asr_manager.listen_once()
            self.event_queue.put(event)
        except Exception as exc:
            print(f"[ASR] 后台识别失败: {exc}")


def run():
    camera = create_camera_provider()
    if not camera.start():
        print("[WARN] Camera initialization failed")

    ENABLE_YOLO       = os.getenv("ENABLE_YOLO",       "1") == "1"
    ENABLE_CONTROLLER = os.getenv("ENABLE_CONTROLLER", "1") == "1"
    ENABLE_ASR        = os.getenv("ENABLE_ASR",        "1") == "1"
    ENABLE_VLM        = os.getenv("ENABLE_VLM",        "1") == "1"
    ENABLE_TTS        = os.getenv("ENABLE_TTS",        "1") == "1"

    if ENABLE_YOLO:
        try:
            yolo_backend = CONFIG.get("yolo.backend", "auto")
            yolo_model_name = CONFIG.get("yolo.model", "yolov8n")
            yolo_onnx_path = CONFIG.get("yolo.onnx_path", None)
            yolo_utils.load_yolo_model(yolo_model_name, yolo_backend, yolo_onnx_path)
        except Exception as e:
            print("[FATAL] YOLO model load failed:", e)
            raise

    if ENABLE_TTS:
        try:
            tts_local_utils.load_tts_model("models/piper/zh_CN-huayan-medium.onnx")
        except Exception as e:
            print("[WARN] TTS model load failed:", e)

    if ENABLE_CONTROLLER:
        controller = Controller(
            spatial_parser=SpatialParserAdapter(),
            decision_maker=DecisionMaker(),
            speech_manager=SpeechManager(
                min_interval=CONFIG.get("speech.min_interval", 2.0),
                stable_count=CONFIG.get("speech.stable_count", 3)),
            enable_logging=False,
        )
    else:
        controller = None

    if ENABLE_ASR and ENABLE_CONTROLLER:
        asr = ASRManager()
        async_asr = AsyncASRInput(asr)
    else:
        asr = None
        async_asr = None

    if os.environ.get("EDGE_VISION_DASHBOARD") and ENABLE_CONTROLLER:
        from observe import dashboard
        dashboard.start(controller)

    print("系统已启动。按 'a' 开始语音输入，按 'q' 退出。")
    if os.name == "nt":
        print("[MODE] Windows (GUI enabled)")
    elif os.environ.get("DISPLAY"):
        print("[MODE] Linux GUI mode")
    else:
        print("[MODE] Headless (Pi / no display)")
    if ENABLE_CONTROLLER:
        controller._play_startup_message()
    frame_count = 0
    detect_interval = CONFIG.get("yolo.detect_interval", 2)
    fps_limit = CONFIG.get("system.fps_limit", 30)

    try:
        while True:
            ret, frame = camera.read()
            if not ret:
                continue

            if ENABLE_YOLO or ENABLE_CONTROLLER:
                frame_count += 1

            if frame_count % detect_interval == 0:
                if ENABLE_YOLO:
                    conf = CONFIG.get("yolo.conf_threshold", 0.5)
                    imgsz = CONFIG.get("yolo.imgsz", 320)
                    objects = yolo_utils.detect_objects(frame, conf_threshold=conf, imgsz=imgsz)
                    yolo_utils.draw_boxes(frame, objects)
                else:
                    objects = []

                if ENABLE_CONTROLLER:
                    controller.context["current_image"] = encode_frame_as_data_url(frame.copy())
                    nav_event = {
                        "type": "navigation",
                        "data": {
                            "objects": objects,
                            "frame_shape": frame.shape,
                        },
                        "timestamp": time.time(),
                    }
                    result = controller.handle_event(nav_event)

                    if result and result.get("speech", {}).get("spoken"):
                        print(f"[{result['mode']}] 导航播报中...")

            if ENABLE_ASR and ENABLE_CONTROLLER:
                user_event = async_asr.poll_event()
                if user_event:
                    print(f"[ASR] 识别结果: {user_event.get('data', {}).get('text', '')}")
                    res = controller.handle_event(user_event)
                    print(f"[System] 模式: {res['mode']}, 回复: {res.get('response')}")

            # Headless Pi 兼容：无 DISPLAY 时跳过 GUI
            if os.environ.get("DISPLAY") or os.name == "nt":
                if os.environ.get("EDGE_VISION_DASHBOARD") and ENABLE_CONTROLLER:
                    _draw_hud(frame, controller)
                cv2.imshow("Edge Vision", frame)
                wait_ms = max(1, int(1000.0 / fps_limit))
                key = cv2.waitKey(wait_ms) & 0xFF
            else:
                key = -1
                time.sleep(max(1.0 / fps_limit, 0.01))

            if ENABLE_CONTROLLER:
                controller._poll_vlm_results()
                controller._drain_arbitrator()

            if ENABLE_ASR and ENABLE_CONTROLLER and key == ord("a"):
                started = async_asr.start_listening()
                if started:
                    print("\n[ASR] 已启动后台语音采集...")
                else:
                    print("\n[ASR] 语音采集中，请等待当前识别完成...")
            elif key == ord("q"):
                break

            # Headless Pi: stdin 'q' + Enter 退出（Windows 不启用 select）
            if os.name != "nt":
                dr, _, _ = select.select([sys.stdin], [], [], 0)
                if dr:
                    line = sys.stdin.readline().strip()
                    if ENABLE_ASR and ENABLE_CONTROLLER and line.lower() == "a":
                        async_asr.start_listening()
                    elif line.lower() == "q":
                        print("[INFO] Quit signal received")
                        break

    finally:
        if ENABLE_CONTROLLER:
            controller.speech.stop()
        if ENABLE_ASR and ENABLE_CONTROLLER:
            async_asr.close()
            asr.close()
        camera.stop()
        cv2.destroyAllWindows()
        if ENABLE_CONTROLLER:
            controller.save_log("run_log.json")
            print("[System] 日志已保存至 run_log.json")


def main():
    try:
        run()
    except Exception as e:
        print(f"[FATAL] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
