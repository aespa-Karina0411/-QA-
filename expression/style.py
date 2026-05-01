"""风格控制：intent → 文本后处理"""


def emergency_style(text: str) -> str:
    text = text.rstrip("。，！")
    return "⚠️ " + text + "！"


def normal_style(text: str) -> str:
    return text.rstrip("。，！") + "。"


STYLE_RULES = {
    "EMERGENCY_WARNING": emergency_style,
    "APPROACHING": normal_style,
    "ENVIRONMENT_DESC": normal_style,
    "STATUS_UPDATE": normal_style,
}
