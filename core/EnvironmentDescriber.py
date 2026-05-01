
class EnvironmentDescription:
    """表达层：负责将结构化的环境上下文转化为自然语言描述。"""

    def generate(self, context: dict) -> str:
        """
        根据 Controller 传入的 context 生成环境描述文本。
        """
        env_data = context.get("scene", {}).get("objects", {})
        objects = (env_data if isinstance(env_data, list) else env_data.get("objects", []))
        
        if not objects:
            return "当前视野内没有检测到明显的目标。"

        # 具体的表达逻辑（如：只取前三个、距离排序、句式拼接）全部在此实现
        parts = []
        # 按距离或优先级排序的逻辑可以放在这里（如果外部没排好序的话）
        for obj in objects[:3]:  # 仅播报最重要的前三个
            direction = obj.get("direction", "前方")
            name = obj.get("class_zh", "物体")
            dist = obj.get("distance", "未知")
            count = obj.get("count", 1)
            
            count_text = f"{count}个" if count > 1 else ""
            parts.append(f"{direction}有{count_text}{name}，距离{dist}")

        return "；".join(parts) + "。"

    def generate_empty_state(self) -> str:
        return "目前一片空旷，您可以放心前行。"