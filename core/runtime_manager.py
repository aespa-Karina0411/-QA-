# runtime_manager.py
"""
统一运行时管理器：集中决策各模块使用 local 还是 cloud 模式。

设计原则：
  - 所有模块（ASR/TTS/VLM）禁止自行判断网络状态，必须通过本类获取运行模式
  - 网络状态采用缓存 + 后台定时更新机制，避免每次调用都触发网络检测
  - 策略可扩展（性能限制、电量状态、用户模式等），当前仅实现最小可用版本
"""

import threading
import time
import socket


class RuntimeManager:
    _instance = None
    _init_lock = threading.Lock()

    # ── 单例 ──────────────────────────────────────────────
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    # ── 初始化 ────────────────────────────────────────────
    def __init__(self, check_interval: float = 5.0, network_timeout: float = 1.0):
        # 防止单例被多次 __init__
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self._check_interval = check_interval
        self._network_timeout = network_timeout

        # ---- 网络状态缓存 ----
        self._online: bool = False
        self._last_check_ts: float = 0.0
        self._online_lock = threading.Lock()

        # ---- 模块策略注册表 ----
        #   prefer_cloud : 在线用 cloud，离线 fallback local
        #   prefer_local : 始终用 local（未来扩展）
        #   adaptive     : 综合电量/性能/网络质量（未来扩展）
        self._strategies: dict[str, str] = {
            "asr": "prefer_cloud",
            # "tts": "prefer_cloud",   # 未来扩展
            # "vlm": "prefer_cloud",   # 未来扩展
        }

        # ---- 首次检测，确保启动时就有可用状态 ----
        self._do_network_check()

        # ---- 后台守护线程：定时刷新网络缓存 ----
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="RuntimeManager-NetworkMonitor",
            daemon=True,
        )
        self._monitor_thread.start()

        print(
            f"[RuntimeManager] 初始化完成 | 间隔={check_interval}s | "
            f"初始网络={'在线' if self._online else '离线'} | "
            f"策略={self._strategies}"
        )

    # ── 网络：底层检测（只在后台线程中执行）──────────────
    def _probe_network(self, host: str = "8.8.8.8", port: int = 53) -> bool:
        try:
            socket.setdefaulttimeout(self._network_timeout)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((host, port))
            return True
        except (socket.error, OSError):
            return False

    def _do_network_check(self):
        new_status = self._probe_network()
        with self._online_lock:
            old_status = self._online
            self._online = new_status
            self._last_check_ts = time.time()
        if old_status != new_status:
            print(
                f"[RuntimeManager] 网络状态变更 → "
                f"{'在线' if new_status else '离线'}"
            )

    def _monitor_loop(self):
        while self._running:
            time.sleep(self._check_interval)
            self._do_network_check()

    # ── 公共接口 ──────────────────────────────────────────
    def get_mode(self, module_name: str) -> str:
        """
        查询指定模块的运行模式。

        Args:
            module_name: 模块名称，如 "asr" / "tts" / "vlm"

        Returns:
            "cloud" 或 "local"

        当前决策仅基于网络状态 + 策略偏好；
        未来可在决策层叠加电量、性能、用户手动模式等因子。
        """
        strategy = self._strategies.get(module_name, "prefer_cloud")

        with self._online_lock:
            online = self._online

        # ---- 决策层（最小可用：仅网络）----
        if strategy == "prefer_cloud":
            return "cloud" if online else "local"
        elif strategy == "prefer_local":
            return "local"
        # 未来扩展预留：
        # elif strategy == "adaptive":
        #     battery_ok = self._battery_sufficient()
        #     perf_ok   = self._perf_sufficient()
        #     return "cloud" if (online and battery_ok and perf_ok) else "local"
        else:
            return "local"

    def is_online(self) -> bool:
        """只读缓存，绝不触发检测。"""
        with self._online_lock:
            return self._online

    def register_strategy(self, module_name: str, strategy: str):
        """
        动态注册 / 修改模块策略。

        Args:
            module_name: 模块名称
            strategy: "prefer_cloud" | "prefer_local" | "adaptive"(未来)
        """
        self._strategies[module_name] = strategy
        print(f"[RuntimeManager] 策略注册: {module_name} → {strategy}")

    # ── 生命周期 ──────────────────────────────────────────
    def shutdown(self):
        self._running = False
        print("[RuntimeManager] 已关闭")