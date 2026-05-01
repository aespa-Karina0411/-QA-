<p align="center">
  <h1 align="center">edge-visionQA</h1>
  <p align="center">面向视障人士的 AI 驱动边缘视觉语音问答系统</p>
  <p align="center">
    <strong>YOLO · VLM · ASR · TTS · 多队列仲裁</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.10+-blue" alt="Python">
    <img src="https://img.shields.io/badge/YOLOv8n-实时检测-green" alt="YOLO">
    <img src="https://img.shields.io/badge/VLM-Qwen3.5--Flash-orange" alt="VLM">
    <img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="License">
  </p>
</p>

---

## 项目简介

**edge-visionQA** 是一款面向视障人士的实时边缘视觉语音问答系统。用户佩戴摄像头获取前方图像，通过语音与系统自然交互。系统在边缘端运行 YOLO 实时目标检测 + 多模态 VLM 云端推理，以语音播报方式回答用户的场景理解问题。

**核心能力**：
- 🗣️ **全语音交互**：ASR 语音输入（按住说话）+ TTS 语音输出（实时播报）
- 🎯 **实时环境感知**：YOLOv8n 实时检测行人/车辆/障碍物，结构化语义输出
- 🤖 **视觉问答**：用户语音提问 → VLM 理解图像 → 语音回答
- 🚨 **紧急警告**：WARNING 零丢失保障，硬实时优先级调度
- 🧠 **决策引擎**：5 层决策（State→Behavior→Suppression→Selection→Expression）

---

## 系统架构

### 核心数据流

```
Camera → YOLO                    ASR → IntentParser
    ↓                                 ↓
    └──── Controller.handle_event ────┘
                  ↓
        ┌─────────┼─────────┐
        │ navigation │ user_input │
        └──────┬─────┴─────┬───┘
               │           │
        SpatialParser  IntentParser
               │           │
        DecisionMaker  ┌──┴──────────────┐
               │       │ VLM IntentParser │
        ExpressionEngine  VLMManager(VLM) │
               │       └──────┬──────────┘
               │              │
        ┌──────┴──────┐      │
        │ OutputPolicy│      │
        └──────┬──────┘      │
               │              │
        ┌──────┴──────────────┴──┐
        │   SpeechArbitrator      │
        │   ┌────┐ ┌────┐ ┌───┐   │
        │   │WARN│ │VLM│ │ENV│   │
        │   └─┬──┘ └─┬──┘ └─┬─┘   │
        │     └──┬────┴──┬──┘     │
        │      Scheduler │        │
        └──────────┬─────┘───────┘
                   ↓
            SpeechManager (2-PQ + drain)
                   ↓
              TTSBackend → 用户
```

### 设计原则

| 原则 | 实现 |
|------|------|
| **唯一调度入口** | `Controller.handle_event()` 统一分发所有事件 |
| **唯一状态源** | `Context` 三层隔离：`scene` / `dialog` / `system` |
| **唯一语音出口** | `SpeechArbitrator` 三队列调度，WARNING 零丢失 |
| **纯异步 VLM** | `VLMManager` deque 队列 + `poll_result` 解耦，不阻塞主循环 |

---

## 核心模块

### SpeechArbitrator — 三队列语音仲裁

工业级多队列调度器，替代传统单队列 priority sort 架构：

| 队列 | 容量 | 策略 | 作用 |
|------|------|------|------|
| `WARNING_QUEUE` | 3 | 永不丢弃，满则替换最旧 | 🚨 紧急警告（priority=1） |
| `VLM_QUEUE` | 5 | LIFO 取最新，FIFO 丢旧 | 🤖 VLM 回答（priority=2） |
| `ENV_QUEUE` | 3 | 满则拒新 | 📢 环境播报（priority=3） |

调度器优先级链：`VLM保活 → 硬性交错 → WARNING优先 → ENV降级 → 加权轮询 → 兜底`

### SpeechManager — 两级异步播放

- **消息队列**（`PriorityQueue`）：`_run_consumer` 每 0.5s drain，同 source 消息合并
- **播放队列**（`_play_queue`）：唯一 daemon 线程串行消费，永不并发
- **打断机制**：interrupt 消息跳队打断当前播放

### DecisionMaker — 5 层决策引擎

| 层 | 职责 |
|----|------|
| State | `trackers` 字典追踪每个 `(class_zh, direction)` 的历史序列 |
| Behavior | 基于趋势/danger/duration 判定 4 种 intent |
| Suppression | `repeat_interval` 3s 限频 + `INTENT_LEVEL` 等级比较 |
| Selection | `_score` 加权（紧急 100 + 危险 30 + 很近 20 + 靠近 15） |
| Expression | 独立的模板库 + 随机选择 + 风格后处理 |

### OutputPolicy — 输出策略层

| 规则 | 效果 |
|------|------|
| WARNING 永远放行 | 安全关键路径绝对保障 |
| Speech Budget | 5s 窗口内最多 2 条 |
| ENV 降噪 | 相同 objects 不重复播 |

### SpatialUtils — 距离平滑与迟滞

- 5 帧多数投票 `smooth_distance()`
- 迟滞规则："较近→很近"需 2 帧确认，"很近→较近"需 3 帧确认

---

## 快速开始

### 依赖安装

```bash
pip install opencv-python ultralytics numpy openai dashscope pyaudio pygame tenacity
```

### 模型文件

- [YOLOv8n](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt) 放入项目根目录
- 配置环境变量 `DASHSCOPE_API_KEY`（VLM 云端推理）

### 运行

```bash
python main.py
```

按键操作：
- `a`：按住说话，松开后自动识别
- `q`：退出

### 运行测试

```bash
cd tests
python simulate_log.py    # 生成压测数据
python run_validation.py  # 运行验证 → PASS/FAIL
```

---

## 性能基准

### 极限压力测试（304 条目 / 120s）

| 指标 | 优化前（单队列） | 优化后（三队列） | 变化 |
|------|-----------------|-----------------|------|
| WARNING 丢弃 | 34（73.9%） | **0** | ✅ 完全消除 |
| 顺序违规 | 6 | **0** | ✅ 完全消除 |
| 首次崩溃 | t=0.4s | **永不崩溃** | ✅ |
| VLM 已播放 | 15 | **27** | ▲ +80% |
| 播报间隔 | 1.6s | **1.9s** | 更平稳 |

### 真实链路验证（60s 事件流）

| 场景 | 结果 |
|------|------|
| 系统启动语句 | 100% 播放 ✅ |
| VLM 连续 10 次提问 | 10/10 全部播放 ✅ |
| Cold Start 首次环境播报 | 仅 1 次 ✅ |
| 启动期导航拦截 | 生效 ✅ |

---

## 项目结构

```
edge-visionQA/
├── core/
│   ├── controller.py          # 系统中枢（事件驱动 + 状态管理）
│   ├── intent_parser.py       # 关键词意图解析
│   └── EnvironmentDescriber.py
├── perception/
│   ├── speech_arbitrator.py   # 三队列语音仲裁（核心调度器）
│   ├── speech_manager.py      # 两级 PriorityQueue 异步播放
│   ├── output_policy.py       # 输出策略层（Budget + 降噪）
│   ├── decision_utils.py      # 5 层决策引擎
│   ├── spatial_utils.py       # 结构化语义 + 距离平滑迟滞
│   └── yolo_utils.py          # YOLO 检测封装
├── vlm/
│   ├── vlm_manager.py         # VLM 异步队列（deque + scheduler）
│   ├── vlm_cloud_adapter.py   # 云端 API 适配层
│   ├── vlm_intent_parser.py   # VLM 意图解析 + 结构化 prompt
│   └── providers/
├── expression/
│   ├── expression_engine.py   # 模板库 + 随机选择 + 风格处理
│   ├── templates.py
│   └── style.py
├── tts/
│   └── tts_backend.py         # TTS 抽象接口
├── asr/
│   └── asrmanager.py          # ASR 实时语音识别
├── tests/
│   ├── simulate_log.py        # 压测数据生成（304 条目/120s）
│   ├── log_analyzer.py        # 9 项自动化检测器
│   ├── run_validation.py      # 一键 PASS/FAIL
│   └── test_real_pipeline.py  # 真实链路验证
├── model/
├── main.py                    # 入口
├── config.py
├── requirements.txt
└── README.md
```

---

## 验证体系

系统提供完整的自动化验证工具链：

```bash
cd tests
python simulate_log.py          # 生成极限压测数据
python log_analyzer.py           # 7 项指标自动化检测
python run_validation.py         # 一键 PASS/FAIL
python diagnose_trace.py         # 全链路追踪诊断
```

| 检测器 | 方法 | PASS 条件 |
|--------|------|-----------|
| 重复播报 | 连续两条 played 且 text 相同 | 0 |
| 漏报 | 物体距离从 较远→很近 但未播报 | 0 |
| 抖动 | 距离变化率 > 帧数 50% | ≤1 |
| VLM 打断 | VLM 播放距上一个播放 < 1.5s | 0 |
| VLM 饥饿 | VLM 请求 >5s 未播放 | ≤1 |
| 顺序违规 | 低优先级抢占高优先级 | 0 |
| 队列溢出 | overflow 总次数 | ≤10 |
| 播报频率 | played 平均间隔 | 1.5~5s |

---

## 许可证

[MIT License](LICENSE)
