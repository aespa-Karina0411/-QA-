"""VLM Intent 解析器：将用户口语映射为结构化查询"""


def parse_vlm_intent(text: str, context: dict):
    lowered = text.lower()

    if any(k in lowered for k in ("那是什么", "那个是什么", "这是什么")):
        obj = get_most_relevant_object(context)
        if obj:
            return {"type": "object_query", "target": obj}

    if any(k in lowered for k in ("颜色", "样子", "什么色", "多大", "什么样")):
        obj = get_last_mentioned_object(context)
        if obj:
            return {"type": "detail_query", "target": obj}

    if any(k in lowered for k in ("周围", "环境", "旁边", "附近")):
        return {"type": "scene_query", "target": None}

    return None


def get_most_relevant_object(context: dict):
    objects = _extract_objects(context)
    if not objects:
        return None
    objects.sort(key=lambda o: (0 if o.get("direction") == "前方" else 1))
    return objects[0]


def get_last_mentioned_object(context: dict):
    objects = _extract_objects(context)
    if not objects:
        return None
    return objects[0]


def build_prompt(intent: dict, context: dict):
    if intent["type"] == "object_query":
        obj = intent["target"]
        return (
            f"请描述这个物体：{obj['class_zh']}，"
            f"位置在{obj['direction']}，距离{obj['distance']}"
        )
    if intent["type"] == "detail_query":
        obj = intent["target"]
        return (
            f"请详细描述这个物体的外观特征（颜色、形状、大小等）："
            f"{obj['class_zh']}，位置在{obj['direction']}"
        )
    if intent["type"] == "scene_query":
        return "请描述当前画面中的主要物体和环境"
    return ""


def _extract_objects(context: dict):
    env_data = context.get("scene", {}).get("objects", {})
    if isinstance(env_data, dict):
        return env_data.get("objects", [])
    if isinstance(env_data, list):
        return env_data
    return []
