# config.py
import os

# 阿里云DashScope API密钥（从环境变量读取）
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
if not DASHSCOPE_API_KEY:
    raise ValueError("请设置环境变量 DASHSCOPE_API_KEY")

# ASR 录音参数
SAMPLE_RATE = 16000
CHANNELS = 1
FORMAT = 16          # pyaudio.paInt16 对应的数值
BLOCK_SIZE = 3200

# VLM 模型配置
VLM_MODEL = "qwen3.5-flash"
VLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# TTS 模型配置
TTS_MODEL = "cosyvoice-v1"
TTS_VOICE = "longshuo"      # 默认音色
# TTS_OUTPUT_FILE 已移除，改用临时文件

print("config loaded")