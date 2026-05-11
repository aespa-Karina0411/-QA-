import os
import cv2

from camera.base_provider import CameraProvider
from core.global_config import CONFIG


class PCProvider(CameraProvider):

    def __init__(self):
        self._cap = None
        self._camera_src = os.getenv("CAMERA_SRC", "0")

    def start(self) -> bool:
        width = CONFIG.get("camera.width", 640)
        height = CONFIG.get("camera.height", 480)

        if self._camera_src == "csi":
            pipeline = (
                f"libcamerasrc ! video/x-raw,width={width},height={height}"
                " ! videoconvert ! appsink"
            )
            self._cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if not self._cap.isOpened():
                print("[WARN] GStreamer CSI failed, trying V4L2 device 0")
                self._cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
        else:
            self._cap = cv2.VideoCapture(int(self._camera_src))

        if not self._cap.isOpened():
            print("[WARN] Camera open failed, fallback to 0")
            self._cap = cv2.VideoCapture(0)

        if self._cap.isOpened():
            print("[CAMERA] PCProvider active")
        return self._cap.isOpened()

    def read(self):
        if self._cap is None:
            return False, None
        return self._cap.read()

    def stop(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def is_opened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()
