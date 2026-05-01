"""Expression 引擎：模板选择 + 字段填充 + 风格处理"""

import random

from expression.style import STYLE_RULES
from expression.templates import TEMPLATES


class ExpressionEngine:

    def generate(self, decision: dict) -> str:
        intent = decision["intent"]
        obj = decision["obj"]

        if intent == "STATUS_UPDATE" and obj.get("trend") == "approaching":
            intent = "APPROACHING"

        templates = TEMPLATES.get(intent, [])
        if not templates:
            return ""

        template = random.choice(templates)

        text = template.format(
            direction=obj.get("direction", ""),
            class_zh=obj.get("class_zh", ""),
            distance=obj.get("distance", "")
        )

        if intent in STYLE_RULES:
            text = STYLE_RULES[intent](text)

        return text
