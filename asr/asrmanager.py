# asrmanager.py
import time
import socket
import sys
import os

# --- 关键修改：确保能找到父目录的 config 和当前目录的 utils ---
cur_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(cur_dir)

if parent_dir not in sys.path:
    sys.path.append(parent_dir)
if cur_dir not in sys.path:
    sys.path.append(cur_dir)

import config  # 导入 project/config.py

# 导入同级目录下的模块
# 注意：如果上面添加了 cur_dir 到 sys.path，直接 import 即可
try:
    from asr_utils import ASRRecorder       # 云端实现
    from asr_local_utils import LocalASRRecorder  # 本地实现
except ImportError:
    # 备选方案：如果作为包运行，可能需要加点号，但对于直接运行脚本，上面的 sys.path 更稳妥
    from .asr_utils import ASRRecorder
    from .asr_local_utils import LocalASRRecorder


# ---- 关键变更：引入 RuntimeManager ----
from core.runtime_manager import RuntimeManager


class ASRManager:
    """
    ASR 统一管理入口。

    ★ 不再做任何网络判断，只问 RuntimeManager："我该用哪个模式？"
    ★ 运行时云端空结果 / 异常仍可 fallback 到 local（这是容错，不是模式决策）
    """

    def __init__(self, runtime_manager: RuntimeManager | None = None):
        print("[ASRManager] 正在初始化 ASR 输入系统...")

        # ---- 获取全局 RuntimeManager ----
        self.runtime = runtime_manager or RuntimeManager()

        # ---- 初始化底层引擎 ----
        try:
            self.cloud_engine = ASRRecorder()
        except Exception as e:
            print(f"[ASRManager] 云端引擎初始化失败 (跳过): {e}")
            self.cloud_engine = None

        try:
            self.local_engine = LocalASRRecorder()
        except Exception as e:
            print(f"[ASRManager] 本地引擎初始化失败: {e}")
            self.local_engine = None

        if not self.cloud_engine and not self.local_engine:
            raise RuntimeError("无可用 ASR 引擎，请检查配置和模型路径。")

    # ★ _is_online() 已删除 —— 网络判断权收归 RuntimeManager

    def listen_once(self) -> dict:
        """
        执行一次语音采集与识别。
        Returns: 符合 Controller 事件流格式的字典
        """
        mode = self.runtime.get_mode("asr")

        if mode == "cloud" and not self.cloud_engine:
            mode = "local"
        if mode == "local" and not self.local_engine:
            mode = "cloud" if self.cloud_engine else "local"

        print(f"\n>>> 识别模式: {'【在线·云端】' if mode == 'cloud' else '【离线·本地】'} <<<")

        result_text = ""
        source = mode

        try:
            if source == "cloud":
                result_text = self.cloud_engine.record_question_ptt()
                if not result_text and self.local_engine:
                    source = "local"
                    result_text = self.local_engine.record_question_ptt()
            else:
                result_text = self.local_engine.record_question_ptt()
        except Exception as e:
            print(f"[ASRManager] 识别过程异常: {e}")
            if source == "cloud" and self.local_engine:
                source = "local"
                result_text = self.local_engine.record_question_ptt()

        # --- 核心修改：返回标准的事件格式 ---
        return {
            "type": "user_input",
            "data": {
                "text": result_text if result_text else "",
            },
            "timestamp": time.time(),
        }

    def close(self):
        if self.cloud_engine:
            self.cloud_engine.close()
        if self.local_engine:
            self.local_engine.close()
