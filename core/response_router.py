"""响应路由器：用户意图 → 最合适应答模块（不破坏现有调度）"""


class ResponseRouter:

    INDOOR_KEYWORDS = ["椅子", "桌子", "电脑", "床", "显示器", "沙发", "冰箱", "电视"]
    OUTDOOR_KEYWORDS = ["汽车", "公交车", "卡车", "摩托车", "自行车", "树", "路灯"]

    def route(self, intent_result, context):
        intent = intent_result.intent
        if not intent:
            return "FALLBACK"

        if intent == "env_query":
            return self._handle_env_query(context)

        elif intent == "object_query":
            return self._handle_object_query(intent_result, context)

        elif intent in ("general_qa", "describe_environment", "scene_qa", "assistant_query"):
            return "VLM"

        elif intent == "navigation":
            return "DECISION"

        return "FALLBACK"

    # ==================================================================
    # ENV_QUERY：室内/室外判断
    # ==================================================================
    def _handle_env_query(self, context):
        objects = context.get("scene", {}).get("objects", {})
        if isinstance(objects, dict):
            objects = objects.get("objects", [])
        if not objects:
            return {"type": "text", "text": "当前环境信息不足，无法判断室内或室外。"}

        indoor_score = 0
        outdoor_score = 0
        for obj in objects:
            name = obj.get("class_zh", "")
            if name in self.INDOOR_KEYWORDS:
                indoor_score += 1
            if name in self.OUTDOOR_KEYWORDS:
                outdoor_score += 1

        if indoor_score > outdoor_score:
            return {"type": "text", "text": "你当前处于室内环境。"}
        elif outdoor_score > indoor_score:
            return {"type": "text", "text": "你当前处于室外环境。"}
        else:
            return {"type": "text", "text": "环境特征不明显，无法确定室内或室外。"}

    # ==================================================================
    # OBJECT_QUERY：基于当前 scene objects 直接回答
    # ==================================================================
    def _handle_object_query(self, intent_result, context):
        target = intent_result.slots.get("object") if intent_result.slots else None

        objects = context.get("scene", {}).get("objects", {})
        if isinstance(objects, dict):
            objects = objects.get("objects", [])
        if not objects:
            return {"type": "text", "text": "当前没有检测到任何物体。"}

        # 指定目标 → 精确查找
        if target:
            for obj in objects:
                if obj.get("class_zh") == target:
                    return {
                        "type": "text",
                        "text": f"{target}在{obj['direction']}，距离{obj['distance']}。",
                    }
            return {"type": "text", "text": f"当前没有检测到{target}。"}

        # 未指定目标 → 列出所有物体
        if len(objects) <= 3:
            descs = [f"{o['class_zh']}在{o['direction']}{o['distance']}" for o in objects]
            return {"type": "text", "text": "当前检测到：" + "；".join(descs) + "。"}
        else:
            descs = [f"{o['class_zh']}在{o['direction']}" for o in objects[:3]]
            return {"type": "text", "text": "当前检测到：" + "；".join(descs) + f"等{len(objects)}个物体。"}
