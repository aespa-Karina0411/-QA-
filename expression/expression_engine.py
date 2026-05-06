"""Expression 引擎：模板选择 + 字段填充 + 风格处理"""

import random

from expression.style import STYLE_RULES
from expression.templates import TEMPLATES
from expression.navigation_advisor import suggest_avoid_direction
from perception.spatial_utils import DANGER_CLASSES


class ExpressionEngine:

    def generate(self, decision: dict) -> str:
        intent = decision["intent"]
        obj = decision["obj"]

        if intent == "STATUS_UPDATE" and obj.get("trend") == "approaching":
            intent = "APPROACHING"

        direction = obj.get("direction", "")
        distance = obj.get("distance", "")
        class_zh = obj.get("class_zh", "")

        # 导航语义：对 ENVIRONMENT_DESC 和 APPROACHING 生成避让建议
        avoid_dir = ""
        if intent in ("ENVIRONMENT_DESC", "APPROACHING"):
            avoid_dir = suggest_avoid_direction(direction, distance)
            if avoid_dir:
                # 危险物体 + 近距离 → 强化警告语气
                if class_zh in DANGER_CLASSES and distance in ("很近", "较近"):
                    intent = f"{intent}_GUIDED_DANGER"
                else:
                    intent = f"{intent}_GUIDED"

        templates = TEMPLATES.get(intent, [])
        if not templates:
            return ""

        template = random.choice(templates)

        kwargs = {
            "direction": direction,
            "class_zh": class_zh,
            "distance": distance,
        }
        if avoid_dir:
            kwargs["avoid_dir"] = avoid_dir

        text = template.format(**kwargs)

        base_intent = intent.replace("_GUIDED_DANGER", "").replace("_GUIDED", "")
        if base_intent in STYLE_RULES:
            text = STYLE_RULES[base_intent](text)

        return text
