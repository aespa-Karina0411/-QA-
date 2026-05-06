"""
tts_local_utils.py
离线TTS工具模块(基于Piper)

特性：
- 单例加载模型（避免重复加载）
- 支持同步/异步播放
- 支持打断当前播放（适用于实时系统）
- 自动清理临时音频文件
"""

import os
import time
import wave
import threading
import tempfile
import uuid
import platform

from piper import PiperVoice

# 可选播放器（推荐）
try:
    import pygame
    pygame.mixer.init()
    _PYGAME_AVAILABLE = True
except Exception:
    _PYGAME_AVAILABLE = False
    print("[WARN] pygame not available, falling back to blocking playback")


def play_audio_file(wav_path):
    system = platform.system()
    try:
        if system == "Windows":
            os.system(f'start "" "{wav_path}"')
        else:
            ret = os.system(f'aplay "{wav_path}"')
            if ret != 0:
                ret2 = os.system(f'paplay "{wav_path}"')
                if ret2 != 0:
                    print("[WARN] aplay and paplay both failed, no audio device?")
    except Exception as e:
        print("[ERROR] play_audio_file failed:", e)


# ========================
# 全局变量（单例）
# ========================
_voice = None
_currently_playing = None
_lock = threading.Lock()


# ========================
# 模型加载
# ========================
def load_tts_model(model_path: str):
    global _voice
    if _voice is None:
        print("[INFO] Loading TTS model:", model_path)
        _voice = PiperVoice.load(model_path)
        print("[INFO] TTS model loaded")
    return _voice


# ========================
# 文本 → wav 文件
# ========================
def text_to_wav(text: str, output_path: str):
    voice = _voice
    if voice is None:
        raise RuntimeError("[ERROR] load_tts_model() must be called first")

    with wave.open(output_path, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)


# ========================
# 同步播放（阻塞）
# ========================
def play_audio_blocking(wav_path: str):
    if _PYGAME_AVAILABLE:
        try:
            pygame.mixer.music.load(wav_path)
            pygame.mixer.music.play()
            start = time.time()
            while pygame.mixer.music.get_busy():
                if time.time() - start > 10:
                    print("[WARN] pygame playback timeout")
                    break
                time.sleep(0.05)
            return
        except Exception as e:
            print("[WARN] pygame playback failed:", e)

    play_audio_file(wav_path)


# ========================
# 异步播放（可打断）
# ========================
def play_audio_async(wav_path: str):
    global _currently_playing

    def _play():
        if _PYGAME_AVAILABLE:
            try:
                pygame.mixer.music.load(wav_path)
                pygame.mixer.music.play()
                return
            except Exception as e:
                print("[WARN] pygame playback failed:", e)

        threading.Thread(
            target=play_audio_file,
            args=(wav_path,),
            daemon=True
        ).start()

    with _lock:
        # interrupt current playback
        if _PYGAME_AVAILABLE and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()

        t = threading.Thread(target=_play, daemon=True)
        t.start()
        _currently_playing = t


# ========================
# 主接口：文本播报
# ========================
def speak(text: str, async_mode: bool = True):
    """
    直接播报文本（推荐使用这个函数）

    :param text: 要播报的文本
    :param async_mode: 是否异步（默认True）
    """

    if not text.strip():
        return

    # 临时文件
    tmp_path = os.path.join(
        tempfile.gettempdir(),
        f"tts_{uuid.uuid4().hex}.wav"
    )

    # 生成语音
    text_to_wav(text, tmp_path)

    # 播放
    if async_mode:
        play_audio_async(tmp_path)
    else:
        play_audio_blocking(tmp_path)

    # background cleanup
    def _cleanup(path):
        try:
            time.sleep(3)
            if os.path.exists(path):
                os.remove(path)
        except:
            pass

    threading.Thread(target=_cleanup, args=(tmp_path,), daemon=True).start()