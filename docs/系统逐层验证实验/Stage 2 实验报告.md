# Stage 2 调度层验证实验报告（Controller + USER_FOCUS）

---

## 1. 实验目的

本阶段将验证层面从 Stage 1（纯 Arbitrator 注入）提升至**完整 Controller 链路**：通过 `Controller.handle_event()` 注入 navigation 和 user_input 事件，验证真实调度链中 USER_FOCUS 机制的行为。

与 Stage 1 的关键差异：

| 维度 | Stage 1 | Stage 2 |
|------|---------|---------|
| 事件入口 | `arbitrator.submit()` | `controller.handle_event()` |
| 调度链路 | 仅 Arbitrator | Controller → SpatialParser → DecisionMaker → OutputPolicy → Arbitrator |
| 被测机制 | 节流耦合、保活路径 | USER_FOCUS 窗口、对话优先级 |
| 输入模拟 | 直接构造 arbitrator item | 构造标准 Event（含 objects/frame_shape） |

---

## 2. 实验方法

### 2.1 事件模型

两类事件通过统一的 `handle_event()` 注入：

**navigation（模拟 YOLO 检测）**：
```python
nav_event = {
    "type": "navigation",
    "data": {
        "objects": [{"class_zh": "行人", "direction": "前方", ...}],
        "frame_shape": (480, 640)
    },
    "timestamp": time.time()
}
```

频率：每 2 秒一次。

**user_input（模拟用户提问）**：
```python
user_event = {
    "type": "user_input",
    "data": {"text": "前面有什么？"},
    "timestamp": time.time()
}
```

触发时刻：t=10s, 40s, 80s，各一次性触发。

### 2.2 场景交替设计

为了持续产生有效的 navigation SUBMIT，实验准备了两种不同的物体组合（前方行人 / 右侧汽车）。每约 30 秒切换一次，触发 `_is_scene_stable()` 的场景变化检测，生成新的 `ENVIRONMENT_DESC` 事件注入调度器。

### 2.3 实验环境

- 创建真实 `Controller` 实例，含 `SpatialParserAdapter`、`DecisionMaker` 及 `SpeechManager`
- TTS 使用 MockTTS（空实现），避免 Piper 模型加载阻塞
- `is_startup_phase` 和 `cold_start_active` 提前关闭，确保实验在正常调度模式下运行
- 3 轮独立实验，每轮 120 秒

### 2.4 分析指标

对每个 USER_FOCUS 窗口（`[t_user, t_user + 5s]`），统计期间 `source="decision"` 的 PLAY 事件数量。预期值：**0**（USER_FOCUS 应完全抑制环境播报）。

---

## 3. 实验结果

### 3.1 核心指标

| 轮次 | nav_submit | nav_play | usr_submit | usr_play | avg_wait |
|------|-----------|----------|-----------|----------|----------|
| Run 1 | 1 | 0 | 3 | 3 | 0.004s |
| Run 2 | 1 | 0 | 3 | 3 | 0.007s |
| Run 3 | 1 | 0 | 3 | 3 | 0.006s |

三轮结果完全一致。

### 3.2 USER_FOCUS 窗口内导航播放数

| USER_FOCUS 窗口 | Run 1 | Run 2 | Run 3 |
|-----------------|-------|-------|-------|
| t=10s~15s | 0 | 0 | 0 |
| t=40s~45s | 0 | 0 | 0 |
| t=80s~85s | 0 | 0 | 0 |

总计 9 个 USER_FOCUS 窗口，**0 次导航播放泄漏**。

### 3.3 验证结果

```
=== STAGE 2 RESULT ===
  PASS: USER_FOCUS 100% effective — no navigation plays during any window
```

---

## 4. 关键现象分析与机制解释

### 4.1 现象：导航事件全量节流丢弃

实验期间仅产生了 1 次 navigation SUBMIT（优先级 3），且该事件在进入调度器后立即被节流杀死：

```
[SUBMIT] id=2f31d8 source=decision priority=3  text=前方出现散步的行人
[DROP]   id=2f31d8 reason=throttled_drop_env
```

**机制解释：Stage 1 节流耦合的延伸影响**

Stage 1 揭示了 `_pop_vlm` 与 `_apply_throttle` 之间的更新-读取耦合导致所有 VLM 任务被节流抑制。本实验证实**该耦合同样影响 ENV 队列（优先级 3）**：

- `_pop_env()`（`speech_arbitrator.py:323`）：设置 `self.last_play_time = now`
- `_apply_throttle()`（`speech_arbitrator.py:247`）：检查 `now - last_play_time >= 1.5s`

由于 `_pop_env` 刚刚将 `last_play_time` 更新为当前时间，节流检查 `0 < 1.5s` 立即失败。优先级 3 的任务在第一个节流周期被直接丢弃（不同于 VLM 的 requeue+drop 两段式）。

### 4.2 现象：用户提问正常响应

三轮实验中所有 9 个 user_input（每轮 3 个）均成功播放（avg_wait ≈ 0.005s）。

**机制解释**：user_input 事件通过 `IntentParser` 解析为 `object_query`，生成的响应以 `source=user_direct`、`priority=2` 进入 VLM 队列。在无其他 VLM 任务的空闲状态中，VLM 保活路径（距离上次 VLM 播放 > 4s）触发 bypass，任务直接播放。

### 4.3 核心限制：节流耦合"抢先"了 USER_FOCUS

实验结果显示，在当前调度策略下，导航事件在进入调度决策阶段后即被节流机制（throttle）优先丢弃，**未进入 USER_FOCUS 判定路径**。

USER_FOCUS 机制（`controller.py:447-450`）的设计原理是：用户提问后 5 秒内，`_on_navigation_event` 检测到 `user_focus.active = True` 后直接返回，不触发 navigation SUBMIT。但在本实验中，**有限的 navigation SUBMIT 在到达 `_on_navigation_event` 的用户焦点检查之前就已经被调度器的节流门控丢弃了**。

换言之，链条是：

```
navigation event → Controller._on_navigation_event
  → SpatialParser → DecisionMaker → ExpressionEngine
  → [节流丢弃] ← 发生在 USER_FOCUS 检查之前
```

实验证实了**低负载 navigation 在节流耦合下无法存活**这一 Stage 1 结论的延伸，但未能实现"让 navigation 在节流之上存活以检验 USER_FOCUS 抑制"的实验目标。

---

## 5. 核心结论

### 结论一：Controller 完整链路正确

通过 `handle_event()` 注入的标准 Event 能完整走通 SpatialParser → DecisionMaker → OutputPolicy → Arbitrator → PLAY 链路，9 个 user_input 全部播放。

### 结论二：节流耦合是全局性的

Stage 1 发现的节流耦合不仅在 VLM 队列（priority=2）生效，在 ENV 队列（priority=3）同样存在。`_pop_env` 更新 `last_play_time` 后立即被 `_apply_throttle` 检查，导致所有非保活路径上的事件被节流杀错。

### 结论三：USER_FOCUS 未得到充分验证

实验在数据层面通过了"窗口内无导航播放"的标准（nav_play = 0），但这一结果主要由节流耦合贡献。USER_FOCUS 机制的独立性需要在导航事件能够通过节流的条件下才能得到验证——这要求实验设计能够产生不受节流影响的导航事件（例如 `force_play=True` 的紧急警告）。

因此，系统在高负载条件下的"用户优先保障"行为，主要由底层节流机制实现，而非 USER_FOCUS 逻辑。换言之：USER_FOCUS 机制在当前调度架构中未实际参与竞争控制，其作用被上游节流策略所遮蔽。

---

## 6. 风险与限制

- **场景切换频率有限**：120 秒内仅产生 1 次 navigation SUBMIT，说明场景变化检测的触发窗口较窄，大部分实验时间内系统处于"场景稳定 → 无新 SUBMIT"的静默状态。
- **USER_FOCUS 未独立验证**：由于节流耦合先于 USER_FOCUS 生效，无法判断在真实高频 navigation 洪流下 USER_FOCUS 是否真的能抑制播报。
- **MockTTS 简化**：真实 TTS 的播放时长（2.5s）和 speech_lock 机制在 MockTTS 下被简化，可能掩盖锁竞争场景下的行为。

---

## 7. 后续计划

| 阶段 | 目标 | 方法 |
|------|------|------|
| Stage 2 补充 | 独立验证 USER_FOCUS | 注入 `force_play=True` 的 navigation，绕过节流 |
| Stage 3 | 全链路实地测试 | 摄像头 + 麦克风 + 真实 TTS |

---

## 附录：实验可复现性

单次实验：

```bash
python scripts/run_stage2_controller.py
```

脚本使用真实 Controller 实例，不修改任何系统代码。输出 `logs/stage2_controller_run{1,2,3}.jsonl`。
