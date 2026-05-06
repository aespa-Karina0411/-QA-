import time
from collections import defaultdict
from core.global_config import CONFIG

# 距离等级数值化，用于计算趋势
DIST_VAL = {"很近": 3, "较近": 2, "较远": 1}

class DecisionMaker:
    INTENT_LEVEL = {
        "ENVIRONMENT_DESC": 0,
        "STATUS_UPDATE": 1,
        "APPROACHING": 2,
        "EMERGENCY_WARNING": 3,
    }

    def __init__(self):
        # 核心存储：{ (类别, 方向): { "history": [], "first_seen": ts, "reported_intents": set() } }
        self.trackers = {}
        self.last_broadcast_time = 0
        self.broadcast_interval = CONFIG.get("decision.broadcast_interval", 2.5)
        
        self.persistence_threshold = 3.0 # 持续 3 秒定义为"持续存在"
        self.history_limit = 5

        self.last_report_time = {}
        self.repeat_interval = CONFIG.get("decision.repeat_interval", 3.0)

    def get_decision(self, env_data: dict) -> dict:
        now = time.time()
        current_objects = env_data.get("objects", [])
        report_items = []
        suppress_reason = None

        # 1. 更新追踪器与意图判定
        active_keys = []
        for obj in current_objects:
            key = (obj["class_zh"], obj["direction"])
            active_keys.append(key)
            dist_score = DIST_VAL.get(obj["distance"], 0)
            
            if key not in self.trackers:
                # [意图：环境描述] - 新目标进入
                self.trackers[key] = {
                    "history": [dist_score],
                    "first_seen": now,
                    "reported_intents": {"ENVIRONMENT_DESC"},
                    "last_dist": dist_score,
                    "last_intent": None,
                }
                intent = "ENVIRONMENT_DESC"
                # ENV 降噪：非危险 + 距离远 → 抑制
                if not obj.get("is_danger") and dist_score <= 1:
                    intent = None
                    suppress_reason = suppress_reason or "env_noise"
            else:
                tracker = self.trackers[key]
                tracker["history"].append(dist_score)
                if len(tracker["history"]) > self.history_limit:
                    tracker["history"].pop(0)
                
                # 趋势判断
                is_approaching = dist_score > tracker["last_dist"]
                duration = now - tracker["first_seen"]
                
                # 优先级判定逻辑
                if obj.get("is_danger") and (dist_score >= 2 or is_approaching):
                    intent = "EMERGENCY_WARNING"
                elif is_approaching:
                    intent = "APPROACHING"
                    obj["trend"] = "approaching"
                elif duration > self.persistence_threshold and "STATUS_UPDATE" not in tracker["reported_intents"]:
                    intent = "STATUS_UPDATE"
                    obj["trend"] = "staying"
                    tracker["reported_intents"].add("STATUS_UPDATE")
                else:
                    intent = None # 暂无显著变化，抑制播报
                    suppress_reason = suppress_reason or "no_change"
                
                tracker["last_dist"] = dist_score

            if intent:
                last_time = self.last_report_time.get(key, 0)
                if now - last_time < self.repeat_interval:
                    suppress_reason = suppress_reason or "repeat_interval"
                    continue
                tracker = self.trackers[key]
                last_level = self.INTENT_LEVEL.get(tracker.get("last_intent"), -1)
                current_level = self.INTENT_LEVEL.get(intent, -1)
                if current_level <= last_level:
                    suppress_reason = suppress_reason or "low_intent_level"
                    continue
                obj["trend"] = obj.get("trend", "")
                report_items.append({
                    "intent": intent,
                    "obj": obj,
                    "priority": 1 if intent == "EMERGENCY_WARNING" else 0
                })

        # 2. 清理消失的目标
        stopped_keys = [k for k in self.trackers if k not in active_keys]
        for k in stopped_keys:
            del self.trackers[k]

        # 3. 冲突与频率控制
        if not report_items:
            return {"text": "", "should_speak": False, "suppress_reason": suppress_reason or "no_object"}

        def _score(item):
            obj = item["obj"]
            score = 0

            if item["intent"] == "EMERGENCY_WARNING":
                score += 100

            if item["intent"] == "APPROACHING":
                score += 40

            if obj.get("distance") == "很近":
                score += 20

            if obj.get("is_danger"):
                score += 30

            if obj.get("trend") == "approaching":
                score += 15

            return score

        top_item = max(report_items, key=_score)
        
        is_urgent = top_item["priority"] == 1
        if is_urgent or (now - self.last_broadcast_time > self.broadcast_interval):
            self.last_broadcast_time = now

            intent = top_item["intent"]
            obj = top_item["obj"]

            top_key = (obj["class_zh"], obj["direction"])
            self.last_report_time[top_key] = now
            self.trackers[top_key]["last_intent"] = intent

            return {
                "intent": intent,
                "obj": obj,
                "priority": top_item["priority"],
                "should_speak": True
            }

        return {"text": "", "should_speak": False}