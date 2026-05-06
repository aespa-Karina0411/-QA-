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

    "APPROACHING_GUIDED": [
        "{direction}{class_zh}正在靠近，建议稍向{avoid_dir}移动",
        "{direction}有{class_zh}向你接近，注意{avoid_dir}方向"
    ],

    "ENVIRONMENT_DESC": [
        "{direction}有{class_zh}",
        "{direction}出现{class_zh}"
    ],

    "ENVIRONMENT_DESC_GUIDED": [
        "{direction}有{class_zh}，建议稍向{avoid_dir}移动",
        "{direction}出现{class_zh}，可以稍向{avoid_dir}移动"
    ],

    "ENVIRONMENT_DESC_GUIDED_DANGER": [
        "{direction}{distance}处有{class_zh}，请注意避让，向{avoid_dir}移动",
        "{direction}{distance}有{class_zh}，注意安全，建议{avoid_dir}绕行"
    ],

    "APPROACHING_GUIDED_DANGER": [
        "{direction}{class_zh}正在靠近，请注意避让",
        "{direction}有{class_zh}向你接近，注意安全"
    ],

    "STATUS_UPDATE": [
        "{direction}{class_zh}仍在附近"
    ]
}
