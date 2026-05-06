"""语音仲裁层：多队列调度系统（工业级）
   WARNING_QUEUE | VLM_QUEUE | ENV_QUEUE + 加权轮询调度器"""

import time
from core.global_config import CONFIG
from observe.trace_logger import TraceLogger

_trace_logger = None


class SpeechArbitrator:

    def __init__(self):
        global _trace_logger
        if _trace_logger is None:
            _trace_logger = TraceLogger()
        self.trace = _trace_logger
        self._warn_max = CONFIG.get("arbitrator.warning_queue_max", 3)
        self._vlm_max = CONFIG.get("arbitrator.vlm_queue_max", 5)
        self._env_max = CONFIG.get("arbitrator.env_queue_max", 3)
        self._vlm_timeout = CONFIG.get("arbitrator.vlm_timeout", 8.0)
        self._vlm_survival = CONFIG.get("arbitrator.vlm_survival_interval", 4.0)
        self._env_rate_limit = CONFIG.get("arbitrator.env_rate_limit", 1.5)
        self._throttle_window = CONFIG.get("speech.throttle_window", 1.5)
        self._vlm_force_interval = CONFIG.get("arbitrator.vlm_force_interval", 4.0)
        self._vlm_survival_force = CONFIG.get("arbitrator.vlm_survival_force", 5.0)
        self._env_blocked_threshold = CONFIG.get("arbitrator.env_blocked_threshold", 3)
        self._vlm_starvation_threshold = CONFIG.get("arbitrator.vlm_starvation_threshold", 4.0)

        # Phase D: USER_FOCUS 强保障保留槽
        self.vlm_reserved_slot = None
        self.vlm_reserved_pending = False

        # --- 三队列独立管理 ---
        self.warning_queue = []
        self.vlm_queue = []
        self.env_queue = []

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
    # THREAD: main only
    # ==================================================================
    def submit(self, item, context=None):
        now = time.time()
        item["enqueue_ts"] = now

        self.trace.log("SUBMIT",
            id=item.get("trace_id"),
            source=item.get("source"),
            priority=item.get("priority", 3),
            ts=now)

        priority = item.get("priority", 3)
        src = item.get("source", "vlm")
        tid = item.get("trace_id", "?")

        # Phase E0: Observation Only — 标记潜在丢弃对象，不执行丢弃
        drop_candidate = False
        drop_reason = None
        if src == "vlm":
            is_low = not (item.get("aging_boost") or item.get("force_play"))
            near_full = len(self.vlm_queue) >= self._vlm_max - 1
            if is_low and near_full:
                drop_candidate = True
                drop_reason = "low_semantic_under_pressure"
                semantic_label = "LOW" if is_low else "NORMAL"
                print(f"[TRACE][DROP_CANDIDATE] id={tid} semantic={semantic_label} queue_size={len(self.vlm_queue)} reason={drop_reason}")
                self.trace.log("DROP_CANDIDATE",
                    id=tid,
                    semantic=semantic_label,
                    queue_size=len(self.vlm_queue),
                    reason=drop_reason)
        item["drop_candidate"] = drop_candidate
        item["drop_reason"] = drop_reason

        # ---- USER_FOCUS 拦截：非 VLM/user_direct/WARNING 全部禁止 ----
        if context and context.get("system", {}).get("user_focus", {}).get("active"):
            is_vlm = src == "vlm"
            is_user_direct = src == "user_direct"
            is_forced = item.get("force_play", False)
            is_warning = priority <= 1
            if not (is_vlm or is_user_direct or is_forced or is_warning):
                print("[USER_FOCUS_BLOCK]", f"id={tid} source={src}")
                self.trace.log("DROP", id=tid, reason="user_focus_block")
                return

        item["source_queue"] = {0: "STARTUP", 1: "WARNING", 2: "VLM", 3: "ENV"}.get(priority, "ENV")

        qs = {"w": len(self.warning_queue), "v": len(self.vlm_queue), "e": len(self.env_queue)}
        print("[TRACE][ARBITRATOR_IN]", f"id={tid} source={src} priority={priority} queues={qs}")

        # ---- VLM 超时检查 ----
        if src == "vlm":
            if now - item["time"] > self._vlm_timeout:
                self.trace.log("DROP", id=tid, reason="expired")
                print("[TRACE] VLM_DROP: expired id=", tid)
                return

        # ---- ENV 速率限制 ----
        if priority == 3:
            if now - self.last_accept_time < self._env_rate_limit:
                self.trace.log("DROP", id=tid, reason="rate_limit")
                return
            self.last_accept_time = now

        # ---- 路由到对应队列 ----
        if priority <= 1:
            self._push_warning(item)
        elif priority == 2:
            if src == "vlm":
                print("[TRACE] VLM_ENQUEUE id=", tid)
            self._push_vlm(item)
        elif priority == 3:
            if len(self.env_queue) >= self._env_max:
                self.trace.log("DROP", id=tid, reason="queue_full")
                return
            self._push_env(item)

        # ---- 背压标记 ----
        self.system_overloaded = len(self.vlm_queue) >= 3

    def _push_warning(self, item):
        if len(self.warning_queue) >= self._warn_max:
            self.warning_queue.pop(0)
        self.warning_queue.append(item)

    def _push_vlm(self, item):
        if len(self.vlm_queue) >= self._vlm_max:
            evicted = self.vlm_queue.pop(0)
            self.trace.log("DROP", id=evicted.get("trace_id", "?"), reason="vlm_fifo_eviction")
        self.vlm_queue.append(item)

    def _push_env(self, item):
        if len(self.env_queue) >= self._env_max:
            return
        self.env_queue.append(item)

    # ==================================================================
    # 调度器核心：select_next（带节流门控）
    # THREAD: main only
    # ==================================================================
    def select_next(self):
        now = time.time()

        # Phase D: USER_FOCUS 强保障 — 保留槽最高优先级
        if self.vlm_reserved_slot is not None:
            item = self.vlm_reserved_slot
            self.vlm_reserved_slot = None
            if self.vlm_reserved_pending:
                tid = item.get("trace_id", "?")
                print(f"[TRACE][USER_FOCUS_OVERWRITE] id={tid}")
            self.vlm_reserved_pending = False
            print("[TRACE][SELECT]", f"id={item.get('trace_id','?')} source={item.get('source','?')} (reserved)")
            self.trace.log("SELECT", id=item.get("trace_id"), ts=now, path="reserved")
            return item

        # Phase D Step 2: VLM 老化提升 — 超时 VLM 强制提升优先级
        for qitem in list(self.vlm_queue):
            wait_time = now - qitem.get("enqueue_ts", now)
            print(f"[DEBUG][VLM_WAIT] id={qitem.get('trace_id','?')} wait={wait_time:.1f}s threshold={self._vlm_starvation_threshold}s")
            if wait_time > self._vlm_starvation_threshold:
                self.vlm_queue.remove(qitem)
                tid = qitem.get("trace_id", "?")
                print(f"[TRACE][VLM_AGING_BOOST] id={tid} wait={wait_time:.1f}s")
                print("[TRACE][SELECT]", f"id={tid} source={qitem.get('source','?')} (aging)")
                qitem["aging_boost"] = True
                qitem["force_play"] = True
                self.trace.log("SELECT", id=qitem.get("trace_id"), ts=now, path="aging")
                return qitem

        item = None
        bypass_throttle = False

        # ---- 1. VLM 保活 ----
        if now - self.last_vlm_play_time > self._vlm_survival and self.vlm_queue:
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
            env_blocked = len(self.vlm_queue) > self._env_blocked_threshold
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
                self.trace.log("SELECT", id=item.get("trace_id"), ts=time.time(), path="normal")

        return item

    def _apply_throttle(self, item, bypass=False):
        """播放节流：WARNING/force_play/bypass 始终放行。"""
        if bypass or item.get("bypass_throttle") or item.get("force_play"):
            return item
        now = time.time()
        if now - self.last_play_time >= self._throttle_window:
            return item

        prio = item.get("priority", 3)
        if prio == 1:
            return item                     # WARNING always pass

        if prio == 2:
            if not item.get("_throttled_once"):
                item["_throttled_once"] = True
                self.vlm_queue.insert(0, item)
                tid = item.get("trace_id", "?")
                self.trace.log("REQUEUE", id=tid, reason="throttle")
            else:
                tid = item.get("trace_id", "?")
                self.trace.log("DROP", id=tid, reason="throttle")
                print("[TRACE] VLM_BLOCKED: reason=throttled id=", tid)
            return None                     # VLM delayed

        # prio == 3
        tid = item.get("trace_id", "?")
        self.trace.log("DROP", id=tid, reason="throttle_env")
        return None                         # ENV dropped

    def _pop_warning(self):
        item = self.warning_queue.pop(0)
        self.last_decision_time = time.time()
        self.last_play_time = time.time()
        return item

    def _pop_vlm(self):
        """Phase D Step 3: VLM Strategy Layer — wait_time 主导评分"""
        now = time.time()

        # force_play 优先脱离评分竞争
        for i, item in enumerate(self.vlm_queue):
            if item.get("force_play"):
                self.vlm_queue.pop(i)
                tid = item.get("trace_id", "?")
                print(f"[TRACE][VLM_FORCE_SELECT] id={tid}")
                self.last_vlm_play_time = now
                self.last_play_time = now
                return item

        best = None
        best_score = -1
        best_idx = -1
        for i, item in enumerate(self.vlm_queue):
            wait_time = now - item.get("enqueue_ts", now)
            score = 0
            if item.get("aging_boost"):
                score += 30
            else:
                score += 10
            score += wait_time * 10
            if score > best_score:
                best_score = score
                best = item
                best_idx = i

        if best_idx >= 0:
            self.vlm_queue.pop(best_idx)
            tid = best.get("trace_id", "?")
            wait = now - best.get("enqueue_ts", now)
            print(f"[TRACE][VLM_SCORE_SELECT] id={tid} score={best_score:.0f} wait={wait:.1f}s")
            self.trace.log("VLM_SCORE_SELECT",
                id=tid,
                wait=round(wait, 2),
                score=round(best_score, 0),
                aging=bool(best.get("aging_boost")))
        self.last_vlm_play_time = now
        self.last_play_time = now
        return best

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
        force = now - self.last_vlm_play_time > self._vlm_force_interval
        user_window = now - self.last_user_query_time < 3.0
        vlm_survival = now - self.last_vlm_play_time > self._vlm_survival_force
        return normal or force or user_window or vlm_survival

    def mark_decision(self):
        self.last_decision_time = time.time()

    def mark_vlm_played(self):
        self.last_vlm_play_time = time.time()
