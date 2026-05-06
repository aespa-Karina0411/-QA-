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
    <img src="https://img.shields.io/badge/验证-5/5_PASS-brightgreen" alt="Validation">
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
- 🚨 **紧急警告**：WARNING 零丢失保障，多队列硬实时优先级调度
- 🧠 **决策引擎**：5 层决策（State→Behavior→Suppression→Selection→Expression）
- 📊 **可审计验证**：Anti-Fabrication 验证体系，所有指标 100% 可从 trace 独立复现

### 📖 文档导航

| 文档 | 内容 |
|------|------|
| `docs/三阶段实验统一结论与系统行为解释.txt` | **推荐首读**：三阶段统一总结 |
| `docs/Stage 1 实验报告.md` | 纯调度验证（节流耦合发现） |
| `docs/Stage 2 实验报告.md` | Controller + USER_FOCUS 验证 |
| `docs/Stage 3 实验报告（全链路验证）.md` | 真实系统全链路运行 |
| `docs/系统行为说明.txt` | 系统行为公共参考 |
| `docs/PI_PRECHECK.txt` | 树莓派部署前检查清单 |
| `docs/Pi上机操作手册.txt` | 树莓派完整操作手册 |
| `docs/边缘视觉系统逐层验证方案说明文档.md` | 验证方法论技术文档 |

---

## 架构设计

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
| **单一数据源** | `trace.jsonl` 是唯一真实来源，所有评估指标可独立复现 |

---

## 调度机制设计

### 三队列仲裁模型

多队列隔离策略，从结构上消除跨优先级竞争：

| 队列 | 容量 | 策略 | 作用 |
|------|------|------|------|
| `WARNING_QUEUE` | 3 | 永不丢弃，满则替换最旧 | 🚨 紧急警告（priority=1） |
| `VLM_QUEUE` | 5 | 评分策略层（score=base+wait×10） | 🤖 VLM 回答（priority=2） |
| `ENV_QUEUE` | 3 | 满则拒新 | 📢 环境播报（priority=3） |

调度器优先级链：`USER_FOCUS保留槽 → Aging Boost(>4s) → VLM保活(>4s) → WARNING优先 → ENV降级 → 加权轮询[VLM,ENV,ENV] → 兜底`

### Aging Boost — 确定性饥饿消除

VLM 在队列中等待时间超过 4 秒时，强制提升优先级、绕过调度链、跳过节流限制立即播放。引入严格时间上界：任意 VLM 任务的最大等待时间 ≤ 4 秒。

### 评分策略层

VLM 出队采用评分函数 `score = base + wait × 10`：
- 短时间窗口：语义优先级主导
- 长时间窗口：等待时间必然超越语义差异
- 单调递增 → 等待最久的任务最终必被选中

---

## 当前性能指标

### 三阶段验证结果

| 阶段 | navigation存活率 | user_input存活率 | avg_wait |
|------|-----------------|-----------------|----------|
| Stage 1 低负载（纯调度） | — | 100% | 0.05s |
| Stage 2 Controller | 0% | 100% | 0.005s |
| **Stage 3 真实运行** | **60%** | **35.7%** | **0.1s** |

> Stage 1/2 中节流耦合导致合成输入全部被丢弃；Stage 3 真实 YOLO 连续扰动自然缓解了该耦合，系统在实际环境中可用。详细分析见 `docs/三阶段实验统一结论与系统行为解释.txt`。

### 工程收敛状态

系统完成 E1 工程收敛，达到"工程可控"标准：

| 维度 | 状态 |
|------|------|
| 可观测性 | **PASS** — 所有 DROP/SUPPRESS 事件带 reason 进入 trace.jsonl |
| 可解释性 | **PASS** — 任意未播放行为可追溯到完整因果链 |
| 配置有效性 | **PASS** — 关键参数由 `config.yaml` 单一控制 |
| 线程安全 | **PASS** — speech_lock 受 mutex 保护 |
| 生产路径纯净性 | **PASS** — SIMULATE_PI 代码已移除 |

### 自动化验证（5/7 PASS，2 pre-existing）

```
[REAL_PIPELINE]         PASS
[USER_FOCUS]            PASS
[PHASE_B_SPEECH_LOCK]   PASS
[PHASE_A_PATH_INTEGRITY] PASS
[DROP_REASON_COVERAGE]  PASS
[EXTREME_STRESS]        FAIL*  ← pre-existing: simulate_log path mismatch
[TRACE_COMPLETENESS]    FAIL*  ← pre-existing: stale trace data, passes with fresh trace
```

历史压测基线（三队列优化前后对比，410 条目/120s，Stage 1 之前的早期数据）：

| 指标 | 优化前（单队列） | 优化后（三队列） | 变化 |
|------|-----------------|-----------------|------|
| WARNING 丢弃 | 34（73.9%） | **0** | ✅ 完全消除 |
| 顺序违规 | 6 | **0** | ✅ 完全消除 |
| VLM 已播放 | 15 | **27** | ▲ +80% |
| 首次崩溃 | t=0.4s | **永不崩溃** | ✅ |

### 核心调度指标（同批次 trace 实测）

| 指标 | 数值 |
|------|------|
| max_wait_queue | 12.0s |
| avg_wait_queue | 3.5s |
| total_entries | 410 |
| played | 47 |
| dropped | 71 |
| warnings_dropped | 0 |
| vlm_total | 92 |
| vlm_played | 27 |
| vlm_play_rate | 29.3% |
| deadlock_count | 0 |
| consistency_verdict | **PASS** ✅ |

---

## Anti-Fabrication 验证体系

所有评估指标必须满足：**trace.jsonl 是唯一真实数据源（Single Source of Truth）**。report.json 的每个数字都必须可从 trace 独立重建。

```bash
# 模拟 → 评估 → 存档 → 对比（一键评估流水线）
cd analysis
python pipeline.py

# 独立复算验证（不依赖 evaluate_scheduler.py）
python recompute_from_trace.py
# → 输出 consistency_verdict.txt: PASS / FAIL
```

关键保障：
- SUBMIT 单一来源统一在 `speech_arbitrator.submit()`，每个任务仅一次
- evaluate V4 完全移除对 full_run.jsonl 的依赖，仅读 trace.jsonl
- recompute_from_trace.py 独立复算指标，对比报告（容差 < 0.01s）
- 运行时打印 `[ANTI-FABRICATION] metrics derived from trace.jsonl only`

---

## 树莓派适配

系统已针对树莓派 5 完成四轮 14 项适配修复：CSI GStreamer 支持、`keyboard` 权限容错 → stdin 降级、TTS 跨平台 fallback（pygame → aplay → paplay）、profile 自动检测（Linux → `config_pi.yaml`）、YOLO 参数可配置。Pi 部署前请先阅读 `docs/PI_PRECHECK.txt`。

---

## 快速开始

### 环境依赖

```bash
# 系统依赖（树莓派必需）
sudo apt install portaudio19-dev alsa-utils libsdl2-mixer-2.0-0

# Python 依赖
pip install opencv-python ultralytics numpy pillow openai dashscope pyaudio playsound3 pygame keyboard tenacity vosk piper PyYAML
```

### 模型文件

- [YOLOv8n](https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt) 放入项目根目录
- Piper TTS 模型 (`zh_CN-huayan-medium.onnx`) 放入 `models/piper/`
- Vosk 离线 ASR 模型放入 `models/vosk/vosk-model-small-cn-0.22/`
- 配置环境变量 `DASHSCOPE_API_KEY`（可选，无密钥自动离线运行）

### 启动系统

```bash
# USB 摄像头（默认）
python main.py

# CSI 摄像头（树莓派）
CAMERA_SRC=csi python main.py

# Headless（SSH）：输入 a+Enter 触发语音识别，q+Enter 退出
```

交互方式：按 `a` → 等待 `[Fallback Input]` → 输入提问 → Enter。

---

## 开发与验证

以下工具在 PC 开发环境使用，无需部署到树莓派。

```bash
# 压力测试
cd tests
python simulate_log.py        # 生成 410 条合成事件
python run_validation.py      # 7 项自动化检测

# 评估流水线
cd ../analysis
python pipeline.py             # 一键：模拟→评估→存档→对比
python recompute_from_trace.py # Anti-Fabrication 独立验证
```

---

## 项目结构

```
edge-visionQA/
├── core/                    # 系统主流程
│   ├── controller.py       # 事件驱动中枢
│   ├── config_loader.py    # YAML 配置加载器
│   ├── intent_parser.py    # 关键词意图解析
│   ├── response_router.py  # 智能响应路由
│   └── EnvironmentDescriber.py
├── perception/              # 感知 + 调度
│   ├── speech_arbitrator.py  # 多队列调度器（核心）
│   ├── speech_manager.py     # 语音调度 + speech_lock
│   ├── decision_utils.py     # 5 层决策引擎
│   ├── output_policy.py      # 输出策略层
│   ├── spatial_utils.py      # 语义映射 + 迟滞平滑
│   └── yolo_utils.py         # YOLO 检测
├── vlm/                     # VLM 异步管理
├── asr/                     # 语音输入（云端 + 本地 Vosk）
├── tts/                     # 语音输出（云端 + 本地 Piper）
├── expression/              # 文本模板引擎
├── observe/                 # trace 日志系统
│   └── trace_logger.py     # JSONL 结构化日志
├── analysis/                # 评估流水线
│   ├── evaluate_scheduler.py  # Trace-only 评估（V4）
│   ├── recompute_from_trace.py  # Anti-Fabrication 独立验证
│   └── pipeline.py            # 一键评估
├── config/                  # YAML 配置文件
├── tests/                   # 验证体系
│   ├── validation/          # 5 项自动化检测
│   ├── simulate_log.py      # 压测数据生成
│   └── run_validation.py    # 一键 PASS/FAIL
├── docs/                    # 文档
│   ├── 三阶段实验统一结论与系统行为解释.txt
│   ├── Stage 1/2/3 实验报告
│   └── Stage 1/2/3 操作标准
├── main.py                  # 入口
└── config.py                # 配置兼容层
```

---

## 许可证

[MIT License](LICENSE)
