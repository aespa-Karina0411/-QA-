# tts_utils.py
import os
import uuid
import time
import threading
import tempfile
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer
import config
# 引入重试库
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# 尝试导入 pygame 用于异步播放
try:
    import pygame
    pygame.mixer.init()
    PYGAME_AVAILABLE = True
    print("[TTS] 已启用 pygame 异步播放")
except Exception as e:
    PYGAME_AVAILABLE = False
    print("[TTS] 警告: pygame 不可用, 将使用系统播放器。", e)

from playsound3 import playsound
from tts_local_utils import play_audio_file

# ====================== 核心优化：重试逻辑 ======================

@retry(
    stop=stop_after_attempt(3),             # 最多重试3次
    wait=wait_exponential(multiplier=1, min=2, max=10), # 指数退避：2s, 4s, 8s
    retry=retry_if_exception_type(Exception), # 遇到任何异常都重试
    reraise=True                            # 耗尽次数后抛出原始异常
)
def _call_tts_api_with_retry(synthesizer, text):
    """
    专门负责网络调用的内部函数。
    将 API 调用独立出来，是为了只对“网络请求”部分进行重试，
    而不触发文件创建或播放逻辑的重复执行。
    """
    return synthesizer.call(text)

# =============================================================

def _wait_and_delete(file_path, stop_event=None):
    """等待播放结束，然后删除临时文件（增加健壮性检查）"""
    if PYGAME_AVAILABLE:
        try:
            # 增加对 mixer 是否初始化的检查
            while True:
                if not pygame.mixer.get_init():
                    break
                if not pygame.mixer.music.get_busy():
                    break
                
                time.sleep(0.1)
                if stop_event and stop_event.is_set():
                    pygame.mixer.music.stop()
                    break
            
            # 尝试卸载，同样需要检查 mixer 状态
            if pygame.mixer.get_init():
                pygame.mixer.music.unload()
        except pygame.error:
            # 如果主线程关闭了 mixer，这里会捕获异常并安静地退出线程
            pass
        except Exception as e:
            print(f"[TTS Thread Error] {e}")

        # 最后尝试删除文件
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"[TTS] 临时文件已删除: {file_path}")
        except Exception:
            pass

def play_audio_async(file_path, interrupt=True):
    """异步播放音频文件"""
    if not PYGAME_AVAILABLE:
        try:
            playsound(file_path)
        except Exception as e:
            print("[WARN] playsound failed:", e)
            threading.Thread(target=play_audio_file, args=(file_path,), daemon=True).start()
        return True

    if interrupt:
        pygame.mixer.music.stop()

    try:
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.play()
        # 启动后台线程清理
        cleanup_thread = threading.Thread(target=_wait_and_delete, args=(file_path,), daemon=True)
        cleanup_thread.start()
        return True
    except Exception as e:
        print(f"[TTS] 播放失败: {e}")
        return False

def text_to_speech(text, api_key=None, voice=None, output_file=None, async_play=False):
    """
    将文本合成为语音，包含重试机制。
    """
    if not text:
        return False

    api_key = api_key or config.DASHSCOPE_API_KEY
    voice = voice or config.TTS_VOICE
    dashscope.api_key = api_key

    # 1. 准备临时文件路径
    if output_file is None:
        temp_dir = tempfile.gettempdir()
        output_file = os.path.join(temp_dir, f"tts_{uuid.uuid4().hex}.mp3")
        auto_delete = True
    else:
        auto_delete = False

    try:
        # 2. 初始化合成器
        synthesizer = SpeechSynthesizer(model=config.TTS_MODEL, voice=voice)
        
        # 3. 执行带重试的 API 调用
        print(f"[TTS] 正在合成语音: \"{text[:15]}...\"")
        audio = _call_tts_api_with_retry(synthesizer, text)
        
        if audio is None:
            print("[TTS] 合成失败：API 返回数据为空")
            return False

        # 4. 保存文件
        with open(output_file, 'wb') as f:
            f.write(audio)
        
        # 5. 播放逻辑
        if async_play:
            return play_audio_async(output_file)
        else:
            if PYGAME_AVAILABLE:
                try:
                    pygame.mixer.music.load(output_file)
                    pygame.mixer.music.play()
                    start = time.time()
                    while pygame.mixer.music.get_busy():
                        if time.time() - start > 10:
                            print("[WARN] pygame playback timeout")
                            break
                        time.sleep(0.05)
                    pygame.mixer.music.unload()
                except Exception as e:
                    print("[WARN] pygame playback failed, falling back:", e)
                    play_audio_file(output_file)
            else:
                try:
                    playsound(output_file)
                except Exception as e:
                    print("[WARN] playsound failed:", e)
                    play_audio_file(output_file)
            
            if auto_delete:
                os.remove(output_file)
            return True

    except Exception as e:
        print(f"[TTS] 最终合成失败（重试耗尽）: {e}")
        # 清理可能产生的残缺文件
        if auto_delete and os.path.exists(output_file):
            try: os.remove(output_file)
            except: pass
        return False