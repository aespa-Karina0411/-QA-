"""TTS 抽象层：统一语音播放接口，解耦具体实现。"""


class TTSBackend:
    def speak(self, text: str, interrupt: bool = False):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


class LocalTTS(TTSBackend):
    def __init__(self):
        from tts import tts_local_utils

        self._tts = tts_local_utils

    def speak(self, text: str, interrupt: bool = False):
        if interrupt:
            self.stop()
        self._tts.speak(text, async_mode=True)

    def stop(self):
        try:
            import pygame

            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass
