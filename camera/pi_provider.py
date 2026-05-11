from camera.base_provider import CameraProvider


class PiProvider(CameraProvider):

    def __init__(self):
        self._picam2 = None
        self._started = False

    def start(self) -> bool:
        try:
            from picamera2 import Picamera2
            self._picam2 = Picamera2()
            config = self._picam2.create_preview_configuration(
                main={"format": "BGR888"}
            )
            self._picam2.configure(config)
            self._picam2.start()
            self._started = True
            print("[CAMERA] PiProvider active")
            return True
        except Exception as e:
            print(f"[ERROR] PiProvider start failed: {e}")
            return False

    def read(self):
        if not self._started or self._picam2 is None:
            return False, None
        try:
            frame = self._picam2.capture_array()
            return True, frame
        except Exception as e:
            print(f"[ERROR] PiProvider read failed: {e}")
            return False, None

    def stop(self):
        if self._picam2 is not None:
            try:
                self._picam2.stop()
                self._picam2.close()
            except Exception:
                pass
            self._picam2 = None
            self._started = False

    def is_opened(self) -> bool:
        return self._started and self._picam2 is not None
