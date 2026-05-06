# asr_local_utils.py
import json
import os
import time
import threading
import pyaudio

import config
from vosk import Model, KaldiRecognizer


# 尝试导入跨平台按键检测库，用于 PTT 模式
try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except Exception as e:
    KEYBOARD_AVAILABLE = False
    print("[WARN] keyboard not available:", e)

if KEYBOARD_AVAILABLE:
    try:
        keyboard.is_pressed('enter')
    except Exception as e:
        KEYBOARD_AVAILABLE = False
        print("[WARN] keyboard requires elevated permissions:", e)

class LocalASRRecorder:
    """本地离线语音识别器 (基于 Vosk)"""
    
    def __init__(self, model_path=None):
        # 优先使用传入路径，否则从 config 读取，最后使用默认路径
        self.model_path = model_path or getattr(config, 'VOSK_MODEL_PATH', "models/vosk/vosk-model-small-cn-0.22")
        self.sample_rate = getattr(config, 'SAMPLE_RATE', 16000)
        self.chunk_size = getattr(config, 'BLOCK_SIZE', 1600)
        
        self.model = None
        self.recognizer = None
        self.mic = None
        self.stream = None
        
        # 状态控制
        self._stop_event = threading.Event()
        self._audio_thread = None
        self.complete_question = ""
        self._closed = False

        self._init_model()

    def _init_model(self):
        """初始化 Vosk 模型"""
        print(f"正在加载本地模型: {self.model_path}...")
        if not os.path.exists(self.model_path):
            print(f"错误：找不到模型文件: {self.model_path}")
            raise FileNotFoundError(f"Model not found at {self.model_path}")
        
        try:
            self.model = Model(self.model_path)
            self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
            print("本地模型加载成功！")
        except Exception as e:
            print(f"模型加载失败: {e}")
            raise

    def start_recording(self):
        """启动本地录音和识别线程"""
        self.complete_question = ""
        self._stop_event.clear()
        
        # 初始化录音设备
        if self.mic is None:
            self.mic = pyaudio.PyAudio()
        
        self.stream = self.mic.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size
        )

        self._audio_thread = threading.Thread(target=self._recognition_loop)
        self._audio_thread.start()
        return True

    def _recognition_loop(self):
        """后台识别循环"""
        try:
            while not self._stop_event.is_set():
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                if len(data) == 0:
                    continue
                
                if self.recognizer.AcceptWaveform(data):
                    result = json.loads(self.recognizer.Result())
                    text = result.get("text", "").replace(" ", "")
                    if text:
                        self.complete_question += text
                else:
                    # 如果需要实时打印，可以在这里处理 PartialResult
                    pass
        except Exception as e:
            print(f"[LocalASR] 录音线程出错: {e}")

    def stop_recording(self, timeout=2):
        """停止录音并获取最终结果"""
        self._stop_event.set()
        if self._audio_thread:
            self._audio_thread.join(timeout=timeout)
        
        # 获取最后残留在缓冲区的结果
        final_result = json.loads(self.recognizer.FinalResult())
        final_text = final_result.get("text", "").replace(" ", "")
        
        # 拼接最后一段话
        if final_text:
            self.complete_question += final_text
        
        # 关闭流
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
            
        return self.complete_question if self.complete_question.strip() else None

    def record_question_ptt(self):
        """按住说话模式 (PTT) - 逻辑与云端版本保持一致"""
        max_tries = 3
        for i in range(max_tries):
            try:
                if KEYBOARD_AVAILABLE:
                    print(f"\n[本地识别] 第 {i+1} 次尝试: 按住【回车键】开始说话...")
                    while not keyboard.is_pressed('enter'):
                        time.sleep(0.05)
                    
                    self.start_recording()
                    
                    while keyboard.is_pressed('enter'):
                        time.sleep(0.05)
                    
                    result = self.stop_recording()
                else:
                    input(f"\n[本地识别] 第 {i+1} 次尝试: 按回车开始...")
                    self.start_recording()
                    input("录音中，按回车结束...")
                    result = self.stop_recording()

                if result and result.strip():
                    return result
                else:
                    print("[LocalASR] 未能识别到有效语音。")
            except Exception as e:
                print(f"[LocalASR] 发生异常: {e}")
                time.sleep(1)
        return None

    def close(self):
        """释放资源"""
        if self._closed:
            return
        self._stop_event.set()
        if self.mic:
            self.mic.terminate()
        self._closed = True
        print("本地识别器已关闭。")