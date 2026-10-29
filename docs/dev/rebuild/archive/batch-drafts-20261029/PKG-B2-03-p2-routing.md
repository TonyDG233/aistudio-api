# PKG-B2-03 P2 Routing / Retry / Tier Admission

- 步骤：B2
- 状态：draft
- 基线：`99c38ba` + `9b5cdea`（目标 worktree `.kilo/worktrees/rebuild-upstream`；docs HEAD 以 `git rev-parse HEAD` 为准）
- 来源：D-04 / D-06 / D-07；D05-006、D05-009、D05-014、D05-021、D05-023、D05-024、D05-026..034、D05-036..040、D05-042..044、D05-046、D05-047；`05 §4.1 B2`；`06 P2`

## 1. 范围

负责：

- 选举合格账号：`enabled`、未过期、`required_tiers`、429/403 冷却；付费模型必须 paid 账号；只保留一套 surface（chat/build）与 tier 准入，不做 groups/tags/affinity/打分。
- 候选排序：sticky 当前健康账号 + 简单 LRU/FIFO；不使用旧策略组、tag、权重、affinity、公平分。
- `surface/tier` 准入：B2 只实现 `chat`；无显式前缀默认 `chat`；`build` 在 C1 前显式拒绝或标记 `surface_unavailable`，不得静默换面/降级。
- 重试：`retry_same` 必须先于换号生效；204/瞬态 404 同账号重试；429 按“同账号重试 → 唤醒/切现有实例 → 保存请求后重试 → 耗尽 503”；已出正文不切号。
- 403 领域错误：付费模型 tier 不足只做准入拒绝；`TierRequiredError` 携带 `tier/model/required_tier/current_tier`，由 API 层按协议映射；应用层不得依赖 FastAPI。
- 请求期不切号：tier/403 拒绝路径禁止 `try_switch_account`、`activate_account`、`switch_auth`、浏览器重开或 Free 降级；429 路径只允许在既有实例间切换并受 `max_switches` 约束。
- D-07 配置：`rotator` 深合并默认值 + 启动校验；请求路径不得因缺键抛 `KeyError`。
- `active_requests` 由 P3 提供；本包只获取/释放句柄，不复制计数逻辑，不另建并发原语。

不负责：BrowserSession/Page 生命周期（PKG-B2-02）、Build（C1）、统计/UI、真实账号测试。

退出条件：tier/403 拒绝路径零切号；429 耗尽只返回 503；D-07 请求期零 `KeyError`；无 lease/pinned/blue-green；离线用例全过。

**Out-of-scope（534 与目标树交叉验证）**：旧 fork 的 `routing_pipeline.py`、`surface_orchestrator.py`、`channel_registry.py`、`instance_pool.py`、lease/reservation、pinned slot、blue-green、页面多开全部不得引入；目标树没有这些模块，禁止按旧路径移植。

## 2. 文件域

- `src/aistudio_api/application/account_orchestrator.py`、`account_rotator.py`：候选/重试主流程、`retry_same`、`max_switches`、tier/paid 准入、429/403 冷却、403 不切号。
- `src/aistudio_api/application/api_service_gemini.py`、`src/aistudio_api/domain/errors.py`：`TierRequiredError`、错误分类、429 内部重试、耗尽 503、403 字段体；应用层不 import FastAPI。
- `src/aistudio_api/infrastructure/gateway/model_discovery.py`：paid 规则 → accessModes → required_tiers；空规则不得回退 Ultra-only。
- `src/aistudio_api/infrastructure/gateway/model_defaults.py`、`src/aistudio_api/api/app.py`：D-07 `rotator` 深合并/启动校验与领域错误映射；与 P9-pre/B1/P1 共享，冲突风险高。
- 测试：`tests/unit/test_model_tier_admission.py`（新）、`test_routing_retry.py`（新）、`test_account_orchestrator.py`、`test_tier_preemption.py`、`test_api_responses.py`、`test_model_defaults.py`。

## 3. 依赖

- PKG-B2-02 冻结 `ensure_ready` 与 `active_requests` 句柄；本包不持有 lease、不建第二页。
- B1/P9-pre 完成 `rotator` 深合并与启动校验；D-04/D-06 的 403 字段与协议映射先冻结，OpenAI/Claude 在 C3 复用同一领域错误。
- 离线 fake rotator/fake client/fake account store；禁止访问真实账号、IP、Google 或真实 429 端点。

## 4. 测试

- `test_model_tier_admission.py`：paid 规则命中且当前 Free → 403，体含 `tier/model/required_tier/current_tier`；`switch_auth`/`activate_account` 调用次数为 0；离线。
- `test_model_tier_admission.py`：paid 但无规则 → 按 accessModes/paid 判定，不得回退 Ultra-only；空规则不误 403；403 与 429/503 不混用、不触发浏览器重开；离线。
- `test_routing_retry.py`：上游 429 首次同账号重试（`retry_same=True`），再切现有实例，耗尽返回 503（非 429）；超过 `max_switches` 即停并保留最后错误；离线。
- `test_routing_retry.py`：204/瞬态 404 同账号重试且不换号；非瞬态 4xx 不重试；流式已出数据后 429/403 不再切号，按流错误事件输出；离线。
- `test_routing_retry.py`：成功/异常/取消/流关闭均释放 `active_requests`，不泄漏；`surface=build` 在 C1 前显式拒绝，`surface=chat` 正常准入，不隐式换面；离线。
- `test_account_orchestrator.py`、`test_tier_preemption.py`：attempt 0 不因 paid 模型主动抢切；`TierRequiredError` 不调用 `try_switch_account`；应用层无 `HTTPException`；tier 准入不触发 `switch_auth`；离线。
- `test_api_responses.py`、`test_model_defaults.py`：Gemini/Responses 403 字段体、429→503 耗尽、错误码不互串；`rotator` 缺块/缺键深合并默认、启动校验显式报错、请求期无 `KeyError`；离线。
- 授权要求：全部离线；真实 429/账号切换测试另走真实测试闸门。

## 5. 命令与证据

```
cd .kilo/worktrees/rebuild-upstream
uv run pytest tests/unit/test_model_tier_admission.py tests/unit/test_routing_retry.py tests/unit/test_account_orchestrator.py tests/unit/test_tier_preemption.py tests/unit/test_api_responses.py tests/unit/test_model_defaults.py -q
uv run ruff check src/aistudio_api/application/account_orchestrator.py src/aistudio_api/application/account_rotator.py src/aistudio_api/application/api_service_gemini.py src/aistudio_api/domain/errors.py
```

证据路径：`docs/dev/evidence/B2/2026-10-28/PKG-B2-03/`（pytest/junit、403/429/503 请求-响应样本、switch 调用计数）。脱敏：禁止 API Key、cookie/token、账号邮箱、IP、真实模型请求参数入证据。

## 6. 失败停止

- 403 路径触发 `switch_auth`/`activate_account`/浏览器重开；429 耗尽仍返回 429；403 与 503 混用；请求期出现 `KeyError`。
- 引入 lease/reservation/pinned/blue-green/页面多开；或绕过 `active_requests` 另建并发原语。
- 单包内修复重试上限 2 次；仍失败即停止并保留失败证据，不扩大范围。
- 需要改 paid tier 集合、403 字段位置、429 耗尽错误码或 D-07 配置契约时，先向用户请求裁决。

## 7. 回滚

- 回滚点：本包开始前的目标 worktree HEAD；仅还原上述 src/tests 文件，不整树 checkout。
- 无数据迁移；若临时改动 `rotator_state.json` 结构，先备份并恢复旧文件；不合并 master/rebuild，不 commit。

## 8. 未决

- B2 的 `build` surface 是直接 503，还是等 C1 前保持未实现错误；需用户确认，避免与 P4 重叠。
- paid 账号允许集合（Pro/Ultra/Plus/Ultra_20X）与“paid 但无规则”的回退口径，需按 C06 裁决。
- 403 体四字段在 Gemini/Responses 中的精确位置；OpenAI/Claude 在 C3 接入时是否保持同一 schema。
- 429 耗尽的错误码/文案（`switch_exhausted` 或 `quota_exhausted`）及是否允许任何场景透传 429。
- D-07 深合并/启动校验归 P9-pre 还是本包；`model_defaults.py`/`api/app.py` 为共享文件，需先定主改方。
