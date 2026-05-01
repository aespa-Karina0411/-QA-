"""模板映射：intent → 文本模板"""

TEMPLATES = {

    "EMERGENCY_WARNING": [
        "{direction}{class_zh}很近，请注意",
        "注意，{direction}{class_zh}距离很近",
        "{direction}有{class_zh}，非常接近"
    ],

    "APPROACHING": [
        "{direction}{class_zh}正在靠近",
        "{direction}有{class_zh}向你接近"
    ],

    "ENVIRONMENT_DESC": [
        "{direction}有{class_zh}",
        "{direction}出现{class_zh}"
    ],

    "STATUS_UPDATE": [
        "{direction}{class_zh}仍在附近"
    ]
}
