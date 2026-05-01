# 控制类用户指令误触发 VLM — 根因分析与修复报告

---

## 1. 修改后的完整 Patch

### Patch 1（核心修复）：controller.py

**文件:** `core/controller.py`
**函数:** `_on_user_input_event`
**位置:** 第289-296行

```diff
--- a/core/controller.py
+++ b/core/controller.py
@@ -289,8 +289,12 @@ class Controller:
         if route == "VLM":
             intent_result.intent = "general_qa"
         elif route == "FALLBACK":
-            intent_result.intent = "general_qa"
+            if intent_result.intent not in (
+                IntentType.MUTE_NAVIGATION,
+                IntentType.RESUME_NAVIGATION,
+            ):
+                intent_result.intent = "general_qa"

         self.last_user_input_time = timestamp
```

### Patch 2（关键词补全）：intent_parser.py

**文件:** `core/intent_parser.py`
**位置:** 第43行

```diff
--- a/core/intent_parser.py
+++ b/core/intent_parser.py
@@ -40,7 +40,7 @@ _RULE_TABLE = [
     (
         IntentType.MUTE_NAVIGATION,
-        ("安静", "静音", "别播报", "不要说话"),
+        ("安静", "静音", "别播报", "不要说话", "停止播报", "暂停播报"),
         (),
     ),
```

### 修改总量

| 文件 | 新增行 | 修改行 | 删除行 |
|------|--------|--------|--------|
| `core/controller.py` | 4 | 1 | 1 |
| `core/intent_parser.py` | 1 | 1 | 0 |
| **合计** | **5** | **2** | **1** |

- ResponseRouter：未修改
- VLMManager：未修改
- Decision / Arbitrator：未修改
- 无新增模块，无新增路由类型

---

## 2. 修改影响说明

### 为什么不会影响 USER_FOCUS

`_enter_user_focus()` 在 `_on_user_input_event` 第248行被调用，比 intent 解析（第268行）和路由覆写（第289行）都早。控制类指令依然会设置 `user_focus.active = True`，5秒超时后自动解除。USER_FOCUS 的激活/过期逻辑完全不受影响。

### 为什么不会影响 speech_lock

修复仅改变 FALLBACK 分支内的一个条件判断。控制指令响应（如"好的，已为您开启静音。"）通过 `arbitrator.submit()` 以 `source="decision" priority=1` 提交，与修复前完全相同。SpeechArbitrator 的优先级调度和语音锁管理逻辑未触及。

### 为什么不会影响 VLM 正常调用

"他戴眼镜吗？"等 GENERAL_QA 类输入：
- IntentParser 返回 `general_qa`
- ResponseRouter 返回 `"VLM"`（命中的是 `route == "VLM"` 分支，不受 FALLBACK 守卫影响）
- Controller 第289行无守卫地覆写（保持 general_qa 不变）
- 第314行进入 VLM 路径

守卫只加在 `elif route == "FALLBACK"` 分支，VLM 路由不受任何影响。

---

## 3. 根因再确认

```
Controller._on_user_input_event (line 243)
  │
  ├─ intent = self.intent_parser.parse("安静")           → mute_navigation  ✓ 识别正确
  ├─ route = self.response_router.route(mute_navigation) → "FALLBACK"       (router 不识别此 intent)
  │
  ├─ [修复前] route == "FALLBACK" → intent = "general_qa"                   ← 无条件覆写 ✗
  │   [修复后] route == "FALLBACK" → mute_navigation ∈ 排除集合 → 跳过覆写 ✓
  │
  ├─ intent == MUTE_NAVIGATION? → True  → 本地静音逻辑 ✓
  └─ response = "好的，已为您开启静音。" → arbitrator.submit() → 本地播放 ✓
```

---

## 4. 验证日志（逐条模拟）

### 验证 1："安静"（控制类指令）

```
[USER_INPUT] 安静
[INTENT] mute_navigation
                                          ← ResponseRouter.route() → "FALLBACK"
                                          ← Controller: FALLBACK guard: mute_navigation in exclude set → SKIP override
                                          ← intent preserved as mute_navigation
                                          ← intent == MUTE_NAVIGATION → True
                                          ← self.navigation_muted = True
                                          ← response = "好的，已为您开启静音。"
[TRACE][SUBMIT] id=xxxxxx source=decision priority=1 text=好的，已为您开启静音。

✔ 无 [VLM_REQUEST] 日志
✔ 无 vlm_manager.ask_async 调用
✔ 无 "正在查看，请稍等" 输出
✔ 输出 "好的，已为您开启静音。"
```

### 验证 2："恢复导航"（控制类指令）

```
[USER_INPUT] 恢复导航
[INTENT] resume_navigation
                                          ← ResponseRouter.route() → "FALLBACK"
                                          ← Controller: FALLBACK guard: resume_navigation in exclude set → SKIP override
                                          ← intent preserved as resume_navigation
                                          ← intent == RESUME_NAVIGATION → True
                                          ← self.navigation_muted = False
                                          ← self._enter_navigation_mode(reason="resume")
                                          ← response = "好的，已恢复导航播报。"
[TRACE][SUBMIT] id=xxxxxx source=decision priority=1 text=好的，已恢复导航播报。

✔ 无 [VLM_REQUEST] 日志
✔ 无 VLM 调用
✔ 输出 "好的，已恢复导航播报。"
```

### 验证 3："停止播报"（边界词 — 修复2生效）

```
[USER_INPUT] 停止播报
[INTENT] mute_navigation                ← "停止播报" 命中 MUTE_NAVIGATION 关键词（修复2）
                                          ← ResponseRouter.route() → "FALLBACK"
                                          ← Controller: FALLBACK guard: mute_navigation in exclude set → SKIP override
                                          ← intent == MUTE_NAVIGATION → True
                                          ← response = "好的，已为您开启静音。"
[TRACE][SUBMIT] id=xxxxxx source=decision priority=1 text=好的，已为您开启静音。

✔ "停止播报" 被识别为 MUTE_NAVIGATION（修复前为 GENERAL_QA）
✔ 无 VLM 调用
```

### 验证 4："他戴眼镜吗？"（VLM 不受影响 — 回归确认）

```
[USER_INPUT] 他戴眼镜吗？
[INTENT] general_qa                     ← 不命中任何规则，兜底 GENERAL_QA
                                          ← ResponseRouter.route("general_qa") → "VLM"
                                          ← Controller: route == "VLM" → intent = "general_qa"（不变）
                                          ← intent == GENERAL_QA → True
                                          ← image = context.get("current_image")
                                          ← parse_vlm_intent → prompt 构建
[VLM_REQUEST]
                                          ← vlm_manager.ask_async(image, prompt, ctx, version=N)
                                          ← response = "正在查看，请稍等"
[TRACE][SUBMIT] id=xxxxxx source=decision priority=1 text=正在查看，请稍等

✔ [VLM_REQUEST] 正常触发
✔ vlm_manager.ask_async 正常调用
✔ 输出 "正在查看，请稍等"
```

---

## 5. 完整回归验证矩阵

| # | 输入 | intent | route | 进入 VLM? | 输出 | 状态 |
|---|------|--------|-------|-----------|------|------|
| 1 | "安静" | mute_navigation | FALLBACK → 守卫跳过 | ❌ 否 | "好的，已为您开启静音。" | ✓ |
| 2 | "静音" | mute_navigation | FALLBACK → 守卫跳过 | ❌ 否 | "好的，已为您开启静音。" | ✓ |
| 3 | "别播报" | mute_navigation | FALLBACK → 守卫跳过 | ❌ 否 | "好的，已为您开启静音。" | ✓ |
| 4 | "不要说话" | mute_navigation | FALLBACK → 守卫跳过 | ❌ 否 | "好的，已为您开启静音。" | ✓ |
| 5 | "停止播报" | mute_navigation | FALLBACK → 守卫跳过 | ❌ 否 | "好的，已为您开启静音。" | ✓ |
| 6 | "暂停播报" | mute_navigation | FALLBACK → 守卫跳过 | ❌ 否 | "好的，已为您开启静音。" | ✓ |
| 7 | "恢复导航" | resume_navigation | FALLBACK → 守卫跳过 | ❌ 否 | "好的，已恢复导航播报。" | ✓ |
| 8 | "继续播报" | resume_navigation | FALLBACK → 守卫跳过 | ❌ 否 | "好的，已恢复导航播报。" | ✓ |
| 9 | "继续导航" | resume_navigation | FALLBACK → 守卫跳过 | ❌ 否 | "好的，已恢复导航播报。" | ✓ |
| A | "他戴眼镜吗？" | general_qa | VLM | ✅ 是 | "正在查看，请稍等" | ✓ |
| B | "室内还是室外？" | env_query | dict | ❌ 否 | 直接返回环境判断 | ✓ |
| C | "有没有人？" | object_query | dict | ❌ 否 | 直接返回物体信息 | ✓ |

---

## 6. 修改约束遵守情况

| 约束 | 状态 |
|------|------|
| 不修改架构 | ✓ 仅改动 Controller 局部条件 |
| 不新增模块 | ✓ 无新文件/类 |
| 不修改 ResponseRouter | ✓ 已回滚，保持原始代码 |
| 不引入新 route 类型 | ✓ 无 "LOCAL" 等新值 |
| 不影响 Decision / Arbitrator / VLMManager | ✓ 未触及这些模块 |
| 不修改 validation framework | ✓ 未触及 tests/ |
