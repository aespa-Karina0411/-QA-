import base64
import os
import queue
import select
import sys
import threading
import time

import cv2
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
    import os
    camera_src = os.getenv("CAMERA_SRC", "0")
    if camera_src == "csi":
        cap = cv2.VideoCapture(
            "libcamerasrc ! video/x-raw,width=640,height=480 ! videoconvert ! appsink",
            cv2.CAP_GSTREAMER
        )
    else:
        cap = cv2.VideoCapture(int(camera_src))
    if not cap.isOpened():
        print("[WARN] Camera open failed, fallback to 0")
        cap = cv2.VideoCapture(0)

    try:
        yolo_utils.load_yolo_model("yolov8n.pt")
    except Exception as e:
        print("[WARN] YOLO model load failed:", e)
    try:
        tts_local_utils.load_tts_model("models/piper/zh_CN-huayan-medium.onnx")
    except Exception as e:
        print("[WARN] TTS model load failed:", e)

    asr = ASRManager()
    async_asr = AsyncASRInput(asr)
    controller = Controller(
        spatial_parser=SpatialParserAdapter(),
        decision_maker=DecisionMaker(),
        speech_manager=SpeechManager(min_interval=2.0, stable_count=3),
        enable_logging=False,
    )

    print("系统已启动。按 'a' 开始语音输入，按 'q' 退出。")
    if os.name == "nt":
        print("[MODE] Windows (GUI enabled)")
    elif os.environ.get("DISPLAY"):
        print("[MODE] Linux GUI mode")
    else:
        print("[MODE] Headless (Pi / no display)")
    controller._play_startup_message()
    frame_count = 0
    detect_interval = CONFIG.get("yolo.detect_interval", 2)
    fps_limit = CONFIG.get("system.fps_limit", 30)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            frame_count += 1
            if frame_count % detect_interval == 0:
                controller.context["current_image"] = encode_frame_as_data_url(frame.copy())
                conf = CONFIG.get("yolo.conf_threshold", 0.5)
                imgsz = CONFIG.get("yolo.imgsz", 320)
                objects = yolo_utils.detect_objects(frame, conf_threshold=conf, imgsz=imgsz)

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

            user_event = async_asr.poll_event()
            if user_event:
                print(f"[ASR] 识别结果: {user_event.get('data', {}).get('text', '')}")
                res = controller.handle_event(user_event)
                print(f"[System] 模式: {res['mode']}, 回复: {res.get('response')}")

            # Headless Pi 兼容：无 DISPLAY 时跳过 GUI
            if os.environ.get("DISPLAY") or os.name == "nt":
                cv2.imshow("Edge Vision", frame)
                wait_ms = max(1, int(1000.0 / fps_limit))
                key = cv2.waitKey(wait_ms) & 0xFF
            else:
                key = -1
                time.sleep(max(1.0 / fps_limit, 0.01))

            controller._poll_vlm_results()
            controller._drain_arbitrator()

            if key == ord("a"):
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
                    if line.lower() == 'a':
                        async_asr.start_listening()
                    elif line.lower() == 'q':
                        print("[INFO] Quit signal received")
                        break

    finally:
        controller.speech.stop()
        async_asr.close()
        asr.close()
        cap.release()
        cv2.destroyAllWindows()
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
