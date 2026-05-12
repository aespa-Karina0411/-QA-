<p align="center">
  <h1 align="center">edge-visionQA</h1>
  <p align="center">面向视障人士的 AI 驱动边缘视觉语音问答系统</p>
  <p align="center">
    <strong>YOLOv8n · Qwen3.6-Flash · Piper · Vosk · 多队列仲裁</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.10+-blue" alt="Python">
    <img src="https://img.shields.io/badge/target-Raspberry Pi 5-red" alt="Pi5">
    <img src="https://img.shields.io/badge/VLM-Qwen3.6--Flash-orange" alt="VLM">
    <img src="https://img.shields.io/badge/validation-5/5_PASS-brightgreen" alt="Validation">
    <img src="https://img.shields.io/badge/Pi_stage-Phase 3/yellow" alt="Pi">
    <img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="License">
  </p>
</p>

---

## 项目简介

edge-visionQA 是一款面向视障人士的实时边缘视觉语音问答系统。用户佩戴摄像头 + 耳机，系统自动感知前方环境（行人、车辆、障碍物）并主动语音播报；用户可随时语音提问，系统调用云端视觉大模型理解图像后以语音回答。

系统不是"调几个 API"的集成项目——核心贡献是在树莓派 5 级别的资源约束下，通过多队列隔离调度机制保证：紧急警告永不丢失、VLM 回答不无限等待、环境播报不压过用户交互。所有调度行为可观测、可追溯、可独立验证。

**当前状态**：PC 三阶段验证 + E1 工程收敛已完成。Pi5 部署正在 Phase 3-4 逐层恢复中（Camera → YOLO → Controller → Speech → TTS → VLM/ASR）。

---

## 架构设计

### Camera Runtime 解耦（Phase 2）

```
main.py
    ↓
CameraProvider (camera/base_provider.py)
    ├── PCProvider   (cv2.VideoCapture + CSI/GStreamer/V4L2 fallback)
    └── PiProvider   (Picamera2 + BGR888 显式配置，懒加载 import)
```

Controller 不直接接触 cv2.VideoCapture 或 Picamera2。`read()` 返回 `(bool, numpy.ndarray, BGR)`。

### 核心数据流

```
CameraProvider → YOLO              ASR → IntentParser
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
| Camera 解耦 | `CameraProvider` 抽象层，Pi/PC 自动选择 backend |
| 唯一调度入口 | `Controller.handle_event()` 统一分发 |
| 唯一状态源 | `Context: scene / dialog / system` 三层隔离 |
| 唯一语音出口 | `SpeechArbitrator` 三队列，WARNING 零丢失 |
| 异步 VLM | `VLMManager` deque + `poll_result`，不阻塞主循环 |
| 单一数据源 | `trace.jsonl`，所有指标可独立复现 |

---

## 调度机制

### 三队列仲裁

| 队列 | 容量 | 策略 | 优先级 |
|------|------|------|--------|
| WARNING | 3 | 永不丢弃，满则覆盖最旧 | 1 |
| VLM | 5 | 评分策略层 (score=base+wait×10) | 2 |
| ENV | 3 | 满则拒新 | 3 |

调度链：`USER_FOCUS保留槽 → Aging Boost(>4s) → VLM保活(>4s) → WARNING优先 → ENV降级 → 加权轮询[VLM,ENV,ENV] → 兜底`

### Aging Boost

VLM 等待 > 4s → 强制提权，绕过调度链 + 节流限制立即播放。任意 VLM 任务最大等待时间 ≤ 4s（确定性消除饥饿）。

---

## Feature Flag 体系（Phase 3+）

支持 Pi5 逐层恢复验证，不重构系统：

| Flag | 控制范围 | Phase 3 | Phase 4 | Phase 5 | Phase 6 | Phase 7 | Phase 8 |
|------|---------|:-------:|:-------:|:-------:|:-------:|:-------:|:-------:|
| `ENABLE_YOLO` | 模型加载、推理、overlay | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ENABLE_CONTROLLER` | Controller、arbitrator、decision | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ |
| `ENABLE_ASR` | 语音采集 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| `ENABLE_VLM` | VLM 入口 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| `ENABLE_TTS` | TTS 模型加载 | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |

所有 flag 默认 `"1"`，不加环境变量时行为与改前完全一致。

---

## 快速开始

### 安装

```bash
# Python 依赖（PC 或 Pi）
pip install -r requirements.txt
```

### PC 开发环境

```bash
python main.py                    # 全系统启动
```

### Pi5 逐层验证

```bash
# Phase 3 — Camera runtime baseline（仅 Camera + GUI）
ENABLE_YOLO=0 ENABLE_CONTROLLER=0 ENABLE_ASR=0 ENABLE_VLM=0 ENABLE_TTS=0 python main.py

# Phase 4 — YOLO 视觉负载验证（Camera + YOLO + overlay + GUI）
ENABLE_YOLO=1 ENABLE_CONTROLLER=0 ENABLE_ASR=0 ENABLE_VLM=0 ENABLE_TTS=0 python main.py

# Phase 8 — 完整系统
python main.py
```

### 其他环境变量

```bash
CAMERA_SRC=csi              # Pi CSI 摄像头
EDGE_VISION_PROFILE=pi      # 强制 Pi profile
EDGE_VISION_DASHBOARD=1     # 终端实时调度面板 + 画面 HUD 叠加
```

---

## Pi5 部署状态

| Phase | 内容 | 状态 |
|-------|------|:----:|
| Phase 2 | CameraProvider 架构解耦（PCProvider/PiProvider/factory） | ✅ 完成 |
| Phase 3 | Camera runtime baseline 稳定性验证（camera_only） | 🔄 实机验证中 |
| Phase 4 | YOLO 视觉负载验证 | ⏳ 待执行 |
| Phase 5-8 | Controller → Speech → TTS → VLM/ASR 逐层恢复 | ⏳ 计划中 |

---

## PC 验证结果

| 阶段 | navigation 存活率 | user_input 存活率 | avg_wait |
|------|:-----------------:|:-----------------:|:--------:|
| Stage 1 低负载（纯调度注入） | — | 100% | 0.05s |
| Stage 2 Controller（合成事件） | 0% | 100% | 0.005s |
| Stage 3 真实运行（5min） | 60% | 35.7% | 0.1s |

> Stage 1/2 节流耦合在合成输入下杀死所有导航事件；Stage 3 真实 YOLO 连续扰动自然缓解。系统在真实环境中可用。

### 压测基线（三队列优化，410 条目/120s）

| 指标 | 优化前 | 优化后 | 变化 |
|------|--------|--------|------|
| WARNING 丢弃 | 34 (73.9%) | 0 | ✅ |
| 顺序违规 | 6 | 0 | ✅ |
| VLM 已播放 | 15 | 27 | +80% |
| 首次崩溃 | t=0.4s | 永不 | ✅ |

### 验证体系（5/5 PASS）

```
REAL_PIPELINE · USER_FOCUS · SPEECH_LOCK · PATH_INTEGRITY · DROP_COVERAGE
```

---

## 项目结构

```
edge-visionQA/
├── camera/                  # Camera 抽象层（Phase 2 新增）
│   ├── base_provider.py    # CameraProvider 接口
│   ├── pc_provider.py      # cv2.VideoCapture 封装
│   ├── pi_provider.py      # Picamera2 封装（懒加载）
│   └── factory.py          # 工厂函数，复用 CONFIG profile
├── core/                    # 系统主流程
│   ├── controller.py       # 事件驱动中枢（530 行）
│   ├── config_loader.py    # YAML 配置 + profile 切换
│   ├── intent_parser.py    # 关键词意图解析
│   ├── response_router.py  # 智能响应路由
│   └── ...
├── perception/              # 感知 + 调度
│   ├── speech_arbitrator.py  # 多队列调度器（核心，340 行）
│   ├── speech_manager.py     # 语音调度 + speech_lock
│   ├── decision_utils.py     # 5 层决策引擎
│   ├── output_policy.py      # 输出策略层
│   ├── spatial_utils.py      # 语义映射 + 迟滞平滑
│   └── yolo_utils.py         # YOLO 检测
├── vlm/                     # VLM 异步管理
├── asr/                     # 语音输入（云端 DashScope + 本地 Vosk）
├── tts/                     # 语音输出（云端 cosyvoice + 本地 Piper）
├── expression/              # 文本模板引擎 + 导航导引
├── observe/                 # trace 日志系统
├── analysis/                # 评估流水线 + Anti-Fabrication
├── config/                  # YAML 配置（config.yaml / pc / pi）
├── tests/                   # 验证体系
├── docs/                    # 文档
├── main.py                  # 唯一入口
└── config.py                # 配置兼容层
```

---

## 文档

| 文档 | 内容 |
|------|------|
| `docs/项目技术手册.txt` | 完整技术手册（592+ 行） |
| `docs/中期答辩技术报告.txt` | 中期答辩报告 |
| `docs/系统行为说明.txt` | 系统行为公共参考 |
| `docs/PI_PRECHECK.txt` | 树莓派部署前检查清单 |
| `docs/Pi上机操作手册.txt` | 树莓派完整操作手册 |
| `docs/日志回收说明.txt` | trace 日志回收步骤 |
| `大创日志.md` | 完整项目开发日志（31 章） |

---

## 许可证

[MIT License](LICENSE)
