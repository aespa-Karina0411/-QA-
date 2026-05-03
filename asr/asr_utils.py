# asr_utils.py
import sys
import os
import time
import threading
import pyaudio

# --- 关键修改：添加父目录到系统路径 ---
cur_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(cur_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

import config  # 现在可以找到 project/config.py 了
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult


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

class ASRRecorder:
    """实时语音识别器，提供录音并返回识别文本的功能"""
    def __init__(self, api_key=None):
        self.api_key = api_key or config.DASHSCOPE_API_KEY
        self.mic = None
        self.stream = None
        self.question_ready = threading.Event()
        self.complete_question = None

        # 用于 start/stop 模式的控制变量
        self._recognition = None
        self._stop_event = None
        self._audio_thread = None
        self._device_ready = threading.Event()

        # 防重复关闭标志
        self.last_error = None  # 新增这一行
        self._closed = False

    def _callback_class(self):
        """返回自定义回调类，绑定到当前实例"""
        class MyCallback(RecognitionCallback):
            def __init__(self, recorder):
                super().__init__()
                self.recorder = recorder

            def on_open(self):
                # 初始化录音设备
                print('录音设备初始化...')
                self.recorder.mic = pyaudio.PyAudio()
                self.recorder.stream = self.recorder.mic.open(
                    format=pyaudio.paInt16,
                    channels=config.CHANNELS,
                    rate=config.SAMPLE_RATE,
                    input=True
                )
                self.recorder._device_ready.set()

            def on_close(self):
                # 避免重复关闭和重复打印
                if self.recorder.stream is None and self.recorder.mic is None:
                    return
                print('录音设备关闭。')
                if self.recorder.stream:
                    self.recorder.stream.stop_stream()
                    self.recorder.stream.close()
                if self.recorder.mic:
                    self.recorder.mic.terminate()
                self.recorder.stream = None
                self.recorder.mic = None

            def on_complete(self):
                print('识别完成。')

            def on_error(self, message):
                # 删掉 sys.exit(1)
                print(f'[ASR] 识别出错（可能由于网络波动）: {message.message}')
                # 可以在这里记录一个错误状态，让主程序知道这次录音无效
                self.recorder.last_error = message.message
                

            def on_event(self, result: RecognitionResult):
                sentence = result.get_sentence()
                if 'text' in sentence:
                    current_text = sentence['text']
                    print('识别文本: ', current_text)
                    if RecognitionResult.is_sentence_end(sentence):
                        self.recorder.complete_question = current_text
                        print('句子结束，完整问题: ', current_text)
                        self.recorder.question_ready.set()

        return MyCallback(self)

    def record_question(self, timeout=5):
        """原始录音方式：自动检测句子结束并返回（保留原功能）"""
        self.question_ready.clear()
        self.complete_question = None

        callback = self._callback_class()
        recognition = Recognition(
            model='fun-asr-realtime-2026-02-28',
            format='pcm',
            sample_rate=config.SAMPLE_RATE,
            semantic_punctuation_enabled=False,
            callback=callback
        )
        recognition.start()

        start_wait = time.time()
        while self.stream is None and time.time() - start_wait < timeout:
            time.sleep(0.1)
        if self.stream is None:
            print("录音设备初始化超时，请检查麦克风。")
            recognition.stop()
            return None

        while not self.question_ready.is_set():
            if self.stream:
                try:
                    data = self.stream.read(config.BLOCK_SIZE, exception_on_overflow=False)
                    recognition.send_audio_frame(data)
                except Exception as e:
                    print("录音出错:", e)
                    break
            else:
                break

        recognition.stop()
        return self.complete_question

    # ====================== start / stop 方法 ======================
    def start_recording(self):
        """启动录音线程，开始发送音频数据。必须在调用 stop_recording() 之前调用。"""
        self.question_ready.clear()
        self.complete_question = None
        self._device_ready.clear()
        self._stop_event = threading.Event()

        callback = self._callback_class()
        self._recognition = Recognition(
            model='fun-asr-realtime-2026-02-28',
            format='pcm',
            sample_rate=config.SAMPLE_RATE,
            semantic_punctuation_enabled=False,
            callback=callback
        )
        self._recognition.start()

        # 等待设备就绪
        start_wait = time.time()
        while not self._device_ready.is_set() and time.time() - start_wait < 5:
            time.sleep(0.1)
        if not self._device_ready.is_set():
            print("录音设备初始化超时，请检查麦克风。")
            self._recognition.stop()
            self._recognition = None
            return False

        self._audio_thread = threading.Thread(target=self._audio_send_loop)
        self._audio_thread.start()
        return True

    def _audio_send_loop(self):
        """后台线程：持续从麦克风读取音频并发送给识别服务"""
        while not self._stop_event.is_set():
            if self.stream:
                try:
                    data = self.stream.read(config.BLOCK_SIZE, exception_on_overflow=False)
                    self._recognition.send_audio_frame(data)
                except Exception as e:
                    print("录音出错:", e)
                    break
            else:
                time.sleep(0.1)

    def stop_recording(self, timeout=5):
        """停止录音，等待识别结果并返回。返回识别到的完整句子，超时返回 None。"""
        if not self._recognition:
            return None

        self._stop_event.set()
        if self._audio_thread:
            self._audio_thread.join(timeout=2)

        self._recognition.stop()
        self._recognition = None

        if self.question_ready.wait(timeout):
            return self.complete_question
        else:
            return None

    # ====================== PTT 模式 ======================
    def record_question_ptt(self):
        """
        优化后的按住说话模式：
        1. 包含自动重试机制 (最多 3 次)。
        2. 完美兼容 keyboard 库可用与不可用的回退逻辑。
        3. 增加了对识别过程中网络错误的捕捉。
        """
        max_tries = 3
        for i in range(max_tries):
            # 每次尝试前重置错误标志和状态
            self.last_error = None
            self.complete_question = None
            
            try:
                if KEYBOARD_AVAILABLE:
                    # ================== 模式 A: 有键盘库 (PTT模式) ==================
                    print(f"\n[第 {i+1} 次尝试] 请按住【回车键】开始说话，松开后自动停止...")
                    
                    # 等待按键按下
                    while not keyboard.is_pressed('enter'):
                        time.sleep(0.05)

                    # 开启录音流
                    if not self.start_recording():
                        print("[ASR] 录音流开启失败，准备重试...")
                        time.sleep(1)
                        continue

                    # 等待按键松开
                    while keyboard.is_pressed('enter'):
                        time.sleep(0.05)
                    
                    # 停止录音并获取结果
                    result = self.stop_recording()

                else:
                    # ================== 模式 B: 无键盘库 (Input模式) ==================
                    print(f"\n[第 {i+1} 次尝试]")
                    input("按回车开始录音...")
                    if not self.start_recording():
                        print("[ASR] 录音流开启失败，准备重试...")
                        time.sleep(1)
                        continue
                        
                    input("录音中，按回车结束录音...")
                    result = self.stop_recording()

                # ================== 结果验证逻辑 ==================
                # 检查回调函数中是否记录了网络错误
                if hasattr(self, 'last_error') and self.last_error:
                    print(f"[ASR] 识别过程中网络中断: {self.last_error}，正在尝试恢复...")
                    time.sleep(1)
                    continue # 触发下一次重试

                if result and result.strip():
                    return result
                else:
                    print("[ASR] 未能识别到有效语音，请重试。")
                    # 只有在最后一次尝试失败时才返回 None
                    if i < max_tries - 1:
                        time.sleep(0.5)
            
            except Exception as e:
                print(f"[ASR] 录音过程发生异常: {e}")
                self.close() # 发生异常时先释放资源
                time.sleep(1)

        return None

    # ====================== 资源释放 ======================
    def close(self):
        """显式释放所有资源，确保音频流和事件循环被正确清理（可安全重复调用）"""
        if self._closed:
            return
        self._closed = True

        # 停止正在进行的识别
        if self._recognition:
            try:
                # 通知后台线程停止
                if self._stop_event:
                    self._stop_event.set()
                if self._audio_thread and self._audio_thread.is_alive():
                    self._audio_thread.join(timeout=1)
                self._recognition.stop()
            except Exception:
                pass
            self._recognition = None

        # 额外等待一小段时间，让 dashscope 内部线程完全退出
        time.sleep(0.1)

        # 关闭 pyaudio 流（如果尚未被 on_close 关闭）
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if self.mic:
            try:
                self.mic.terminate()
            except Exception:
                pass
            self.mic = None