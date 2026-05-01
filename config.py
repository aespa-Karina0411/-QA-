# config.py — 兼容层：保留旧 import，值从 CONFIG 读取或 fallback 到硬编码默认
import os

# 阿里云DashScope API密钥（从环境变量读取，不变）
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
if not DASHSCOPE_API_KEY:
    raise ValueError("请设置环境变量 DASHSCOPE_API_KEY")

# ASR 录音参数（暂不迁移）
SAMPLE_RATE = 16000
CHANNELS = 1
FORMAT = 16          # pyaudio.paInt16 对应的数值
BLOCK_SIZE = 3200

# 从新配置系统读取，fallback 到硬编码默认
from core.global_config import CONFIG

VLM_MODEL = CONFIG.get("vlm.model", "qwen3.5-flash")
VLM_BASE_URL = CONFIG.get("vlm.base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")

TTS_MODEL = CONFIG.get("tts.model", "cosyvoice-v1")
TTS_VOICE = CONFIG.get("tts.voice", "longshuo")

print("config loaded (compat mode)")
