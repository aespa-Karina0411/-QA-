"""语音仲裁层：多队列调度系统（工业级）
   WARNING_QUEUE | VLM_QUEUE | ENV_QUEUE + 加权轮询调度器"""

import time


class SpeechArbitrator:

    def __init__(self):
        # --- 三队列独立管理 ---
        self.warning_queue = []   # priority=1, max=3, 永不去丢(满则替换最旧)
        self.vlm_queue = []       # priority=2, max=5, FIFO 丢最旧
        self.env_queue = []       # priority=3, max=3, 满则拒新

        # --- 调度器状态 ---
        self.cycle = ["VLM", "ENV", "ENV"]   # WARNING 已全局优先，cycle 为普通轮询
        self.cycle_idx = 0
        self.consecutive_warnings = 0          # WARNING 连续播放计数

        # --- 时间追踪（兼容 Controller 接口） ---
        self.last_decision_time = 0.0
        self.last_vlm_play_time = 0.0
        self.last_user_query_time = 0.0
        self.last_accept_time = 0.0
        self.last_play_time = 0.0              # TASK 3: 全局播放间隔追踪
        self.system_overloaded = False

    # ==================================================================
    # 入队（submit）
    # ==================================================================
    def submit(self, item, context=None):
        now = time.time()
        priority = item.get("priority", 3)
        src = item.get("source", "vlm")
        tid = item.get("trace_id", "?")

        # ---- USER_FOCUS 拦截：非 VLM/user_direct/WARNING 全部禁止 ----
        if context and context.get("system", {}).get("user_focus", {}).get("active"):
            is_vlm = src == "vlm"
            is_user_direct = src == "user_direct"
            is_forced = item.get("force_play", False)
            is_warning = priority <= 1
            if not (is_vlm or is_user_direct or is_forced or is_warning):
                print("[USER_FOCUS_BLOCK]", f"id={tid} source={src}")
                return

        item["source_queue"] = {0: "STARTUP", 1: "WARNING", 2: "VLM", 3: "ENV"}.get(priority, "ENV")

        qs = {"w": len(self.warning_queue), "v": len(self.vlm_queue), "e": len(self.env_queue)}
        print("[TRACE][ARBITRATOR_IN]", f"id={tid} source={src} priority={priority} queues={qs}")

        # ---- VLM 8s 超时检查 ----
        if src == "vlm":
            if now - item["time"] > 8.0:
                print("[TRACE][DROP]", f"id={tid} reason=expired")
                return

        # ---- ENV 速率限制 ≥ 1.5s ----
        if priority == 3:
            if now - self.last_accept_time < 1.5:
                print("[TRACE][DROP]", f"id={tid} reason=rejected_rate_limit")
                return
            self.last_accept_time = now

        # ---- 路由到对应队列 ----
        if priority <= 1:
            self._push_warning(item)
        elif priority == 2:
            self._push_vlm(item)
        elif priority == 3:
            if len(self.env_queue) >= 3:
                print("[TRACE][DROP]", f"id={tid} reason=rejected_queue_full(env)")
                return
            self._push_env(item)

        # ---- 背压标记 ----
        self.system_overloaded = len(self.vlm_queue) >= 3

    def _push_warning(self, item):
        """WARNING_QUEUE: max=3, 永不去丢, 满则替换最旧"""
        if len(self.warning_queue) >= 3:
            self.warning_queue.pop(0)
        self.warning_queue.append(item)

    def _push_vlm(self, item):
        """VLM_QUEUE: max=5, FIFO, 满则丢最旧"""
        if len(self.vlm_queue) >= 5:
            self.vlm_queue.pop(0)
        self.vlm_queue.append(item)

    def _push_env(self, item):
        """ENV_QUEUE: max=3"""
        self.env_queue.append(item)

    # ==================================================================
    # 调度器核心：select_next（带节流门控）
    # ==================================================================
    def select_next(self):
        now = time.time()
        item = None
        bypass_throttle = False

        # ---- 1. VLM 保活 ----
        if now - self.last_vlm_play_time > 4.0 and self.vlm_queue:
            item = self._pop_vlm()
            self.consecutive_warnings = 0
            bypass_throttle = True

        # ---- 2. 硬性交错 ----
        elif self.consecutive_warnings >= 1 and self.vlm_queue:
            item = self._pop_vlm()
            self.consecutive_warnings = 0
            bypass_throttle = True

        # ---- 3. WARNING 优先 ----
        elif self.warning_queue:
            item = self._pop_warning()
            self.consecutive_warnings += 1

        else:
            # ---- 4. ENV 降级 ----
            env_blocked = len(self.vlm_queue) > 3
            if env_blocked and self.vlm_queue:
                item = self._pop_vlm()
                self.consecutive_warnings = 0
            else:
                # ---- 5. 加权轮询 ----
                for _ in range(len(self.cycle)):
                    target = self.cycle[self.cycle_idx]
                    self.cycle_idx = (self.cycle_idx + 1) % len(self.cycle)
                    if target == "VLM" and self.vlm_queue:
                        item = self._pop_vlm()
                        self.consecutive_warnings = 0
                        break
                    if target == "ENV" and self.env_queue and not env_blocked:
                        item = self._pop_env()
                        break
                else:
                    # ---- 6. 兜底 ----
                    if self.vlm_queue:
                        item = self._pop_vlm()
                        self.consecutive_warnings = 0
                    elif self.env_queue and not env_blocked:
                        item = self._pop_env()

        # ---- TASK 3: 节流门控 ----
        if item is not None:
            item = self._apply_throttle(item, bypass=bypass_throttle)
            if item is not None:
                tid = item.get("trace_id", "?")
                src = item.get("source", "?")
                print("[TRACE][SELECT]", f"id={tid} source={src}")

        return item

    def _apply_throttle(self, item, bypass=False):
        """播放节流：1.5s 内禁止连续播放非 WARNING 条目。
           WARNING 始终放行；bypass=True（VLM保活/交错）或 item.bypass_throttle 绕过。"""
        if bypass or item.get("bypass_throttle"):
            return item
        now = time.time()
        if now - self.last_play_time >= 1.5:
            return item

        prio = item.get("priority", 3)
        if prio == 1:
            return item                     # WARNING always pass

        if prio == 2:
            if not item.get("_throttled_once"):
                item["_throttled_once"] = True
                self.vlm_queue.insert(0, item)  # requeue at front
                tid = item.get("trace_id", "?")
                print("[TRACE][DROP]", f"id={tid} reason=throttled_requeue")
            else:
                tid = item.get("trace_id", "?")
                print("[TRACE][DROP]", f"id={tid} reason=throttled_drop")
            return None                     # VLM delayed

        # prio == 3
        tid = item.get("trace_id", "?")
        print("[TRACE][DROP]", f"id={tid} reason=throttled_drop_env")
        return None                         # ENV dropped

    def _pop_warning(self):
        item = self.warning_queue.pop(0)
        self.last_decision_time = time.time()
        self.last_play_time = time.time()
        return item

    def _pop_vlm(self):
        # LIFO：取最新入队的 VLM（最有可能在 5s 窗口内）
        item = self.vlm_queue.pop()
        self.last_vlm_play_time = time.time()
        self.last_play_time = time.time()
        return item

    def _pop_env(self):
        item = self.env_queue.pop(0)
        self.last_play_time = time.time()
        return item

    # ==================================================================
    # Controller 接口（保持不变）
    # ==================================================================
    def can_play_vlm(self):
        now = time.time()
        normal = now - self.last_decision_time >= 1.0
        force = now - self.last_vlm_play_time > 4.0
        user_window = now - self.last_user_query_time < 3.0
        vlm_survival = now - self.last_vlm_play_time > 5.0
        return normal or force or user_window or vlm_survival

    def mark_decision(self):
        self.last_decision_time = time.time()

    def mark_vlm_played(self):
        self.last_vlm_play_time = time.time()
