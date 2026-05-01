#核心语义模块（纯语义层）
"""
spatial_utils.py
方向 + 距离 + 优先级解析
职责：- 输入: YOLO检测结果
    - 输出：结构化环境语义信息
"""

from collections import defaultdict, deque
from typing import Dict, List

# 类别中文映射
CLASS_MAP = {
    "person": "行人",
    "car": "汽车",
    "truck": "卡车",
    "bus": "公交车",
    "motorbike": "摩托车",
    "bicycle": "自行车",
    "refrigerator": "冰箱",
    "chair": "椅子",
    "couch": "沙发",
    "bed": "床",
    "tv": "电视",
    "laptop": "笔记本电脑",
    "cell phone": "手机",
    "dog": "狗",
    "cat": "猫",
    "bottle": "瓶子",
}

QUANTIFIER_MAP = {
    "行人": "名",
    "汽车": "辆",
    "卡车": "辆",
    "公交车": "辆",
    "摩托车": "辆",
    "自行车": "辆",
}

# 危险物体（高优先级,仅标记，不做决策）
DANGER_CLASSES = {"car", "truck", "bus", "motorbike"}


# =========================
# 方向判断
# =========================
def get_direction(x_center, frame_width):
    ratio = x_center / frame_width

    if ratio < 0.33:
        return "左侧"
    elif ratio > 0.66:
        return "右侧"
    else:
        return "前方"


# =========================
# 距离估计（基于bbox面积）
# =========================
def get_distance_label(bbox, frame_area):
    x1, y1, x2, y2 = bbox
    area = (x2 - x1) * (y2 - y1)
    ratio = area / frame_area

    if ratio > 0.15:
        return "很近"
    elif ratio > 0.05:
        return "较近"
    else:
        return "较远"


# 距离平滑与迟滞缓存
_distance_history = {}
_distance_state = {}


def smooth_distance(history):
    return max(set(history), key=history.count)


# =========================
# 主函数：生成结构化语义
# =========================
def parse_environment(objects: List[Dict], frame_shape) -> Dict:
    """
    返回结构化环境信息（标准接口）
    """

    h, w = frame_shape[:2]
    frame_area = h * w

    # 分组：(direction, class)
    groups = defaultdict(list)

    for obj in objects:
        cls = obj["class"]
        bbox = obj["bbox"]

        name_zh = CLASS_MAP.get(cls, cls)

        x1, y1, x2, y2 = bbox
        x_center = (x1 + x2) / 2

        direction = get_direction(x_center, w)
        distance = get_distance_label(bbox, frame_area)

        key = (direction, name_zh, cls)
        groups[key].append(distance)

    # =========================
    # 构建结构化输出
    # =========================
    results = []

    for (direction, name_zh, cls), distances in groups.items():
        count = len(distances)

        # 取最近距离（语义层允许）
        if "很近" in distances:
            final_distance = "很近"
        elif "较近" in distances:
            final_distance = "较近"
        else:
            final_distance = "较远"

        item = {
            "class_en": cls,
            "class_zh": name_zh,
            "count": count,
            "direction": direction,
            "distance": final_distance,
            "is_danger": cls in DANGER_CLASSES
        }

        results.append(item)

    # 距离平滑 + 迟滞
    for item in results:
        key = (item["class_zh"], item["direction"])

        if key not in _distance_history:
            _distance_history[key] = deque(maxlen=10)
        _distance_history[key].append(item["distance"])

        history = _distance_history[key]
        smoothed = smooth_distance(history)

        if key not in _distance_state:
            _distance_state[key] = smoothed

        last = _distance_state[key]
        current = smoothed

        if last == "较近" and current == "很近":
            if history.count("很近") < 2:
                current = "较近"
        elif last == "很近" and current == "较近":
            if history.count("较近") < 3:
                current = "很近"

        _distance_state[key] = current
        item["distance"] = current

    # 清理过期目标
    valid_keys = set((obj["class_zh"], obj["direction"]) for obj in results)
    for key in list(_distance_history.keys()):
        if key not in valid_keys:
            del _distance_history[key]
            _distance_state.pop(key, None)

    return {
        "objects": results,
        "timestamp": None  # 可扩展
    }