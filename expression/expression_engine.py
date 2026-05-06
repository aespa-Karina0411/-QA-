"""Expression 引擎：模板选择 + 字段填充 + 风格处理"""

import random

from expression.style import STYLE_RULES
from expression.templates import TEMPLATES
from expression.navigation_advisor import suggest_avoid_direction


class ExpressionEngine:

    def generate(self, decision: dict) -> str:
        intent = decision["intent"]
        obj = decision["obj"]

        if intent == "STATUS_UPDATE" and obj.get("trend") == "approaching":
            intent = "APPROACHING"

        # 导航语义升级：对 ENVIRONMENT_DESC 和 APPROACHING 生成避让建议
        direction = obj.get("direction", "")
        distance = obj.get("distance", "")
        avoid_dir = ""
        if intent in ("ENVIRONMENT_DESC", "APPROACHING"):
            avoid_dir = suggest_avoid_direction(direction, distance)
            if avoid_dir:
                intent = f"{intent}_GUIDED"

        templates = TEMPLATES.get(intent, [])
        if not templates:
            return ""

        template = random.choice(templates)

        kwargs = {
            "direction": direction,
            "class_zh": obj.get("class_zh", ""),
            "distance": distance,
        }
        if avoid_dir:
            kwargs["avoid_dir"] = avoid_dir

        text = template.format(**kwargs)

        if intent.rstrip("_GUIDED") in STYLE_RULES:
            text = STYLE_RULES[intent.rstrip("_GUIDED")](text)

        return text
