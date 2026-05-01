from collections import Counter


def _normalize_name(obj: dict) -> str:
    return (
        obj.get("class_zh")
        or obj.get("label_zh")
        or obj.get("class_name")
        or obj.get("label")
        or obj.get("name")
        or "物体"
    )


def _group_objects(objects: list[dict]) -> list[tuple[str, int, list[str]]]:
    grouped = {}
    for obj in objects:
        name = _normalize_name(obj)
        direction = obj.get("direction", "前方")
        if name not in grouped:
            grouped[name] = {"count": 0, "directions": []}
        grouped[name]["count"] += int(obj.get("count", 1) or 1)
        grouped[name]["directions"].append(direction)

    results = []
    for name, info in grouped.items():
        direction_counter = Counter(info["directions"])
        directions = [item[0] for item in direction_counter.most_common(2)]
        results.append((name, info["count"], directions))
    return results


def local_scene_qa(context, text) -> str:
    # CONTINUE
    env_data = (context or {}).get("scene", {}).get("objects") or {}
    objects = (env_data if isinstance(env_data, list) else env_data.get("objects", []))
    if not objects:
        return "当前环境中没有检测到明显目标。"

    grouped = _group_objects(objects)
    grouped.sort(key=lambda item: item[1], reverse=True)

    lowered = (text or "").strip().lower()
    ask_direction = any(keyword in lowered for keyword in ("哪里", "在哪", "where"))

    parts = []
    for name, count, directions in grouped[:5]:
        count_text = f"{count}个" if count > 1 else ""
        if ask_direction and directions:
            parts.append(f"{name}在{'、'.join(directions)}")
        elif directions:
            parts.append(f"{'、'.join(directions)}有{count_text}{name}")
        else:
            parts.append(f"有{count_text}{name}")

    if not parts:
        return "当前环境中没有可回答的目标信息。"

    if ask_direction:
        return "；".join(parts) + "。"
    return "当前看到" + "，".join(parts) + "。"
