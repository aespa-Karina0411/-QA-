import base64
import queue
import threading
import time

import cv2
from perception.spatial_adapter import SpatialParserAdapter
from asr.asrmanager import ASRManager
from core.controller import Controller
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


def main():
    cap = cv2.VideoCapture(0)

    yolo_utils.load_yolo_model("yolov8n.pt")
    tts_local_utils.load_tts_model("models/piper/zh_CN-huayan-medium.onnx")

    asr = ASRManager()
    async_asr = AsyncASRInput(asr)
    controller = Controller(
        spatial_parser=SpatialParserAdapter(),
        decision_maker=DecisionMaker(),
        speech_manager=SpeechManager(min_interval=2.0, stable_count=3),
        enable_logging=False,
    )

    print("系统已启动。按 'a' 开始语音输入，按 'q' 退出。")
    controller._play_startup_message()
    frame_count = 0
    detect_interval = 2

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            controller.context["current_image"] = encode_frame_as_data_url(frame.copy())

            frame_count += 1
            if frame_count % detect_interval == 0:
                objects = yolo_utils.detect_objects(frame, conf_threshold=0.5, imgsz=320)

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

            cv2.imshow("Edge Vision", frame)
            key = cv2.waitKey(1) & 0xFF

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

    finally:
        controller.speech.stop()
        async_asr.close()
        asr.close()
        cap.release()
        cv2.destroyAllWindows()
        controller.save_log("run_log.json")
        print("[System] 日志已保存至 run_log.json")


if __name__ == "__main__":
    main()
