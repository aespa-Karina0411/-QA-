"""导航建议模块：根据物体方向输出避让方向
纯 Expression 层增强，不涉及调度。"""


def suggest_avoid_direction(direction: str, distance: str = "") -> str:
    """根据物体所在方向，输出建议的避让方向。

    距离为"较远"时返回空字符串，不输出导航建议。
    """
    if distance == "较远":
        return ""

    mapping = {
        "左侧": "右侧",
        "左前方": "右侧",
        "右侧": "左侧",
        "右前方": "左侧",
        "前方": "右侧",
        "正前方": "右侧",
    }
    return mapping.get(direction, "右侧")
