# intent_parser.py
"""
独立意图解析器：负责将用户原始文本转换为结构化意图结果。

职责边界：
  - 只做 text → intent 的映射，不做任何路由或执行
  - 返回结构化结果，供 Controller 消费
  - 关键词表可扩展、可热更新
"""

from dataclasses import dataclass, field


# ── 意图类型常量 ──────────────────────────────────────────
class IntentType:
    MUTE_NAVIGATION = "mute_navigation"
    RESUME_NAVIGATION = "resume_navigation"
    DESCRIBE_ENVIRONMENT = "describe_environment"
    SCENE_QA = "scene_qa"
    GENERAL_QA = "general_qa"
    ASSISTANT_QUERY = "assistant_query"
    # PHASE 1: 智能路由新增
    ENV_QUERY = "env_query"           # 室内/室外/环境类型
    OBJECT_QUERY = "object_query"     # 有没有XX / XX在哪里
    NAVIGATION = "navigation"         # 哪里可以走 / 有没有障碍


# ── 结构化意图结果 ────────────────────────────────────────
@dataclass
class IntentResult:
    """解析后的意图结果，作为 Controller 的输入单元。"""
    intent: str                          # IntentType 中的值
    text: str = ""                       # 原始文本（assistant_query 时需要传给 LLM）
    confidence: float = 1.0              # 预留：未来可做模糊匹配 / 模型打分
    slots: dict = field(default_factory=dict)  # 预留：实体抽取槽位


# ── 关键词规则表 ──────────────────────────────────────────
# 每条规则：(intent, keywords_zh, keywords_en)
_RULE_TABLE: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    (
        IntentType.MUTE_NAVIGATION,
        ("安静", "静音", "别播报", "不要说话"),
        (),
    ),
    (
        IntentType.RESUME_NAVIGATION,
        ("恢复导航", "继续导航", "继续播报", "开始导航"),
        (),
    ),
    (
        IntentType.ENV_QUERY,
        ("室内还是室外", "室内室外", "是室内", "是室外", "在哪里环境", "什么环境"),
        ("indoor", "outdoor", "inside", "outside"),
    ),
    (
        IntentType.OBJECT_QUERY,
        ("有没有", "在哪里", "在哪边", "哪边有", "有没有看到", "看到了", "有没有人", "有", "有没有汽车",
         "有没有车", "有没有自行车", "有没有摩托车", "有没有公交车"),
        ("is there", "are there", "where is", "do you see"),
    ),
    (
        IntentType.NAVIGATION,
        ("哪里可以走", "往哪走", "怎么走", "有没有障碍", "有障碍吗", "可以走吗", "方向"),
        ("which way", "where to go", "obstacle"),
    ),
    (
        IntentType.SCENE_QA,
        ("有什么", "有什么东西", "看到了什么", "这里有什么", "前面有什么", "周围有什么", "附近有什么", "有哪些"),
        ("what is", "what's", "what are", "what do you see", "what can you see"),
    ),
    (
        IntentType.DESCRIBE_ENVIRONMENT,
        ("前面", "左边", "右边", "周围", "附近", "看到"),
        ("where", "describe"),
    ),
]



class IntentParser:
    """
    意图解析器：基于关键词规则表将用户文本映射为 IntentResult。

    设计要点：
      - 对外只暴露 parse() 方法
      - 规则表可动态注册，方便未来扩展（添加新意图、新关键词）
      - 预留 slots / confidence，后续可平滑升级为模型推理
    """
    
    _SCENE_HINTS_ZH = (
        "前面","后面","左边","右边", "周围","附近","这里","现场","环境","画面","视野","看到","看见",
        "有什么","有哪些","哪里","哪边", "在不在","有没有",
    )
    _SCENE_HINTS_EN = (
        "see","around","front","left","right","behind",
        "nearby","environment","scene","view","camera", "where","what is there","what's there",
    )
    _QUESTION_MARKERS_ZH = ("吗", "呢", "么", "什么", "为何", "为什么", "怎么", "如何", "谁", "哪", "多少")
    _QUESTION_MARKERS_EN = ("what", "why", "how", "who", "which", "when", "where", "?")

    def __init__(self):
        # 运行时规则表，拷贝一份以便动态修改
        self._rules: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = list(_RULE_TABLE)

    # ── 核心接口 ──────────────────────────────────────────
    def parse(self, text: str) -> IntentResult:
        """
        将原始文本解析为 IntentResult。
        
        规则优先级：
          1. 显式关键词规则
          2. scene_qa 兜底判断
          3. general_qa 兜底判断

        Args:
            text: 用户原始输入文本

        Returns:
            IntentResult: 结构化意图结果
        """
        text = (text or "").strip()
        if not text:
            return IntentResult(intent=IntentType.GENERAL_QA, text=text)

        lowered = text.lower()

        for intent, keywords_zh, keywords_en in self._rules:
            # 中文关键词：对原文匹配
            if any(kw in text for kw in keywords_zh):
                slots = self._extract_slots(text, intent)
                return IntentResult(intent=intent, text=text, slots=slots)
            # 英文关键词：对小写匹配
            if any(kw in lowered for kw in keywords_en):
                slots = self._extract_slots(text, intent)
                return IntentResult(intent=intent, text=text, slots=slots)

        # 兜底：未命中任何规则 → 交给助手问答
        if self._looks_like_scene_qa(text, lowered):
            return IntentResult(intent=IntentType.SCENE_QA, text=text)
        return IntentResult(intent=IntentType.GENERAL_QA, text=text)

    def _extract_slots(self, text: str, intent: str) -> dict:
        """从文本提取槽位。"""
        slots = {}
        if intent == IntentType.OBJECT_QUERY:
            for cls in ("行人", "汽车", "自行车", "摩托车", "公交车", "人", "车", "狗", "猫"):
                if cls in text:
                    normalized = {"人": "行人", "车": "汽车"}.get(cls, cls)
                    slots["object"] = normalized
                    break
        return slots

    def _looks_like_scene_qa(self, text: str, lowered: str) -> bool:
        """兜底判断是否属于环境问答。"""
        has_scene_hint = any(kw in text for kw in self._SCENE_HINTS_ZH) or any(
            kw in lowered for kw in self._SCENE_HINTS_EN
        )
        has_question_marker = any(kw in text for kw in self._QUESTION_MARKERS_ZH) or any(
            kw in lowered for kw in self._QUESTION_MARKERS_EN
        )

        if has_scene_hint and has_question_marker:
            return True

        scene_patterns = (
            "有没有",
            "在哪",
            "在哪里",
            "哪边",
            "什么东西",
        )
        return any(pattern in text for pattern in scene_patterns)

    # ── 动态扩展 ──────────────────────────────────────────
    def register_rule(
        self,
        intent: str,
        keywords_zh: tuple[str, ...] = (),
        keywords_en: tuple[str, ...] = (),
    ):
        """
        动态注册新规则或追加关键词。

        若 intent 已存在，合并关键词；否则新增规则。
        """
        for i, (existing_intent, zh, en) in enumerate(self._rules):
            if existing_intent == intent:
                merged_zh = tuple(set(zh) | set(keywords_zh))
                merged_en = tuple(set(en) | set(keywords_en))
                self._rules[i] = (intent, merged_zh, merged_en)
                print(f"[IntentParser] 合并关键词: {intent} → zh={merged_zh}, en={merged_en}")
                return

        self._rules.append((intent, keywords_zh, keywords_en))
        print(f"[IntentParser] 注册新规则: {intent} → zh={keywords_zh}, en={keywords_en}")

    def list_rules(self) -> list[dict]:
        """调试用：返回当前所有规则。"""
        return [
            {"intent": intent, "keywords_zh": list(zh), "keywords_en": list(en)}
            for intent, zh, en in self._rules
        ]