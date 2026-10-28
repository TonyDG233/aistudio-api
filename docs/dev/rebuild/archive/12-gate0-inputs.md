# 12 Gate 0.5 Inputs（P9-pre 前置）

用途：把 P9-pre 的输入冻结成可执行契约。Gate 0.5 未通过，不得进入 P9-pre 编码。行为权威为 `08-09-decision-record.md`（D-01..D-14）与 `06-decision-log.md`（P2/P3），事实源为 `08-08`；本页只收敛接口、状态、schema 与验收模板。

## 1. 目标 seam / 五层接口

请求实例固定：`1 账号 + 1 Context + 1 Page + 1 surface`。控制面只做登录/保活，不占请求容量。

```python
class AccountService(Protocol):
    async def resolve(self, account_id: str) -> Account: ...
    async def eligible(self, account_id: str, surface: Surface, tier: Tier) -> bool: ...
    async def cookie_for(self, account_id: str) -> CookieState: ...

class InstanceFactory(Protocol):
    async def create(self, account_id: str, surface: Surface, min_tier: Tier) -> Instance: ...
    async def rebuild(self, instance: Instance) -> Instance: ...  # 仅显式关闭旧实例后

class SurfaceResolver(Protocol):
    def parse(self, model: str, api_format: ApiFormat) -> Surface: ...  # 只认 chat/build
    def tier(self, model: str) -> Tier: ...  # ModelCatalog 单一所有者

class Executor(Protocol):
    async def acquire(self, surface: Surface, tier: Tier) -> ActiveHandle: ...
    async def release(self, handle: ActiveHandle) -> None: ...  # finally

class EventSink(Protocol):
    def request(self, event: RequestEvent) -> None: ...
    def attempt(self, event: AttemptEvent) -> None: ...
    def usage(self, event: UsageEvent) -> None: ...
```

接口红线：

- `ActiveHandle.active_requests` 是唯一并发账本；无 lease/reservation/30s/15s 预算。
- 手动关闭/手动改绑前检查 `active_requests`；大于 0 可拒绝。**禁用账号/应用预设的 rebind 不得直接拒绝**：必须进入 `RETIRING`、停止新请求、有界等待（超时强制关闭）后重建。成功、失败、取消、断连、流关闭都在 `finally` 释放。
- 账号有活动实例时 Cookie 回写走实例页；否则才允许独立控制页。
- 同账号 chat/build 不并存；切换 surface 必须显式关闭旧实例再重建。
- 后台自动 rebind/rebalance 不实现；手动显式关闭旧实例再重建允许；禁用账号/应用预设必须走同一有界 rebind（D-05）。
- 路由候选包含 SLEEPING/UNSTARTED；消息到达先唤醒合格实例；候选只含 `AccountMeta.enabled=true`。
- 账号分组/预设（99c38ba 原生）只改变 `enabled/group`，不改变 Context/Page/surface 数量；预设不得触发后台改绑或批量重建；notes/批量分组 API 不实现（D-13）。
- 配额记账键由 `quota_scope` 决定：`account`（默认，chat/build 共用）或 `account_surface`（`surface::model`）；只影响记账键，不新增实例（D-01）。
- 页面清理按 D-11：关闭所有非当前请求、非当前活跃账号的 Page/Context；drain 只发生在旧 Page/Context 上且有界超时，期间不新建新页；不得泄露其他账号/surface 的页。
- pipe watchdog 保留（D-02，仅孤儿进程回收）；CDP 只保留 WebSocket ping（D-03，删除 liveness checker/轮询）。
- API/管理页鉴权分离（D-14）：API 只有 `security.api_allow_anonymous`（默认 false）；管理页继续由 `AISTUDIO_WEB_PASSWORD` 控制（空 = 免密，不报错）；不引入 IP/CIDR/设备白名单。

## 2. 实例状态表

| 状态 | 含义 | 允许进入 | 允许离开 |
|---|---|---|---|
| `UNSTARTED` | 有账号配置，未建 browser/context/page | 启动预检、消息唤醒 | `STARTING` |
| `SLEEPING` | 实例已释放页面，可被消息唤醒 | 显式释放/休眠、消息唤醒 | `STARTING` |
| `STARTING` | 正在建 context/page/surface | 消息唤醒、手动重启 | `READY` / `CLOSED` |
| `READY` | 可接请求，`active_requests==0` | 初始化完成 | `BUSY` / `RETIRING` |
| `BUSY` | 有在途请求，`active_requests>0` | acquire；禁用/应用预设可进入 `RETIRING` | `READY`（全部释放）；禁用/应用预设：`RETIRING`（停止新请求，有界等待 `active_requests=0` 或超时强制关闭）；手动关闭/改绑仍可拒绝 |
| `RETIRING` | 正在关闭，不再接新请求 | 手动关闭/改绑、禁用账号、应用预设；禁用/预设可从 `BUSY` 进入（有界等待/超时强制关闭），手动 close 在 `BUSY` 可拒绝 | `CLOSED` |
| `CLOSED` | 已释放；需重建才能再用 | 显式重建 | `STARTING`（仅手动） |

事件与转换：

- 消息到达：`SLEEPING` / `UNSTARTED` 合格实例 → `STARTING` → `READY` → `BUSY`。
- 请求结束：`BUSY` → `READY`（`active_requests` 归零）。**手动关闭/改绑**在 `BUSY` 时可拒绝并提示等待；**禁用账号/应用预设**允许从 `BUSY` 直接进入 `RETIRING`（停止新请求，有界等待 `active_requests=0` 或超时强制关闭），不得要求先等到 `active_requests=0` 才允许进入 `RETIRING`。
- 上游 99c38ba 已移除资源 idle 超时与 idle 回收：不存在定时自动回收；`SLEEPING` 只由显式释放/休眠进入，`active_activity` 置 disable。**watchdog 与 WS ping 是例外**：pipe watchdog 保留（仅孤儿 Chromium 回收），CDP 只保留 WebSocket ping；liveness checker/轮询删除。
- 失败/stale 才手动重启；禁止自动 stale 复活。
- 手动/控制面 rebind（D-05）：`READY`（`active_requests==0`）或收到禁用/应用预设请求 → `RETIRING`（拒绝新请求）→ 停止新请求、`active_requests==0` 或有界等待（超时强制关闭）→ 关闭旧 Page/Context → `CLOSED` → 新建 Context+Page → `STARTING` → `READY`；后台自动 rebind 不存在。修复 toggle/preset 的 fallback 先于 switch 的缺陷。
- 禁止 blue-green：不得先建新 Page 再 drain 旧页。D-10/D-11 只允许在**旧 Page/Context** 上有一次有界 drain，超时强制关闭；drain 期间不新建新页，新页必须在旧页关闭后创建。同一账号最多 1 个 active Page；不得把“1 active + 1 drain”当作页预算例外，也不得出现两个 active Page。
- 429：同账号重试 → 唤醒/切现有实例 → 保存消息后切换再重试 → 耗尽 `503`；已出正文不切号。
- 付费模型无合格付费账号：本地 `403` fast-fail，不发起上游请求；不得与 `503` 混用。

## 3. Config schema（最小字段）

```yaml
instances:
  - account_id: string          # 1 账号
    count: int                  # 实例数，>0
    surface: chat|build         # 1 实例 1 surface
    min_tier: free|pro          # 实例级准入
security:
  api_allow_anonymous: false    # API 免密开关，默认关闭（D-14）
  web_password: ""              # 管理页密码；空 = 免密，不报错（D-14，不引入 IP/CIDR）
api_keys: [string]              # API Key；空列表不等于开放，开放只由 api_allow_anonymous 决定
features:
  formats: [openai|gemini|claude|responses]
  build_surface: bool
keepalive:
  enabled: bool
  interval_hours: 24
logging:
  level: string
  daily_dir: string
  retention_days: 7
timeouts:
  replay: 600                   # upstream 99c38ba 默认
  stream: 600
quota_scope: account            # account | account_surface（D-01；默认 account）
rotator:
  strategy: sticky
  max_retries: 5
  stickiness_bonus: float
  stickiness_threshold: float
  drip_penalty: float
  tier_weights: {Free: 0.0, Plus: 0.5, Pro: 1.0, Ultra: 2.0, Ultra_20X: 3.0}
  cooldowns: {rate_limited_short: 60, rate_limited_drip: 300, rate_limited_drip_extended: 600, auth_error: 300}
paid_model_rules: {}            # contains + required_tiers；来自 defaults/config.yaml
tier_quotas: {}                 # Free/Plus/Pro/Ultra/Ultra_20X 的 daily_limit；软配额
account_presets:
  path: data/accounts/presets.json   # D-08；锁必须可重入/无嵌套死锁，写失败须上报
  items: []                          # filter_tier/filter_group/enable_all；只改 enabled/group
model_defaults:
  profiles: []                  # video/imagen/image 等原生模板
diagnostics:
  hash_include_code: true       # D-09 诊断开关；默认 true，不放进普通用户 UI
```

规则：配置单一 schema（YAML + env + 默认值）；`count/surface/min_tier` 只在实例级；全局只做总上限；关键配置校验/非关键回退默认都要显式记录；不保留 channel registry。
`rotator` 按 D-07 深合并默认：缺整块/缺键都回退默认值，不静默使用残缺块；启动时校验类型与必填，请求路径不得抛 `KeyError`。`paid_model_rules/tier_quotas/account_presets/model_defaults` 缺失时从 `defaults/config.yaml` 初始化。不提供浏览器 idle 超时配置键。
鉴权规则：API 免密只由 `security.api_allow_anonymous` 控制；`api_keys: []` 不等于开放；管理页只由 `web_password`/`AISTUDIO_WEB_PASSWORD` 控制，空值即免密；不引入 IP/CIDR/设备白名单，CF Tunnel 绑定逻辑不变。

## 4. Event schema（P5 只扩展字段）

```json
{
  "request_id": "string",
  "client_request_id": "string",
  "account_id": "string",
  "model": "string",
  "surface": "chat|build",
  "paid": true,
  "api_format": "openai|gemini|claude|responses",
  "attempt": 1,
  "status": "success|error|cancelled",
  "error_class": "string|null",
  "finish_reason": "string|null",
  "latency_ms": 0,
  "usage": {"input": 0, "output": 0, "cache": 0, "reasoning": 0, "count_tokens": 0},
  "ts": "ISO-8601"
}
```

规则：

- `EventSink.request` 记客户端结果；`EventSink.attempt` 记上游每次尝试；两者分母分离，不混算成功率。
- token 汇总 = generation + input + cache，不含 `countTokens`，也不把 `reasoning` 重复计入；`countTokens` 与 `reasoning` 独立计数。
- `finish_reason` 是单次 attempt 的终止原因；`finish_reasons`（dict）是统计聚合字段，归 `D10-*`，不是新的顶层事件。
- `account_breakdown`（按账号的 today_* / quota / cooldown 明细）是 stats REST 响应的派生视图，不是事件类型；不得为它另建账本。
- 配额记账键由 `quota_scope` 决定：`account` 用 `model`，`account_surface` 用 `surface::model`；事件仍保留 `surface`/`model` 原始字段，聚合时按该口径派生，不得另存一份账本。
- 付费模型 tier 不足记 `error_class=tier_insufficient`，错误体/事件摘要必须含 `model/required_tier/current_tier`；不得记为 429/503。
- `finish_reasons` 的统计键按上游新编号表；客户端可见字符串必须保留兼容映射测试；22/CONTINUING 不记终态。
- `hash_include_code` 只影响模板捕获，不是事件字段；严重路由异常时按 D-09 注释优先怀疑该 hash 并回退。
- Gemini usage 的 `cachedContentTokenCount` 归一到 `usage.cache`；`reasoning` 对应 wire usage 的 thinking tokens。
- raw dump 只落 `safe_*`，默认关闭；禁止明文 headers/raw_body；`[toolshape]` 只记 hash/类型/计数。
- P7 只消费事件做聚合；不另起一套账本。

## 5. Acceptance 模板

每个切片提交前填一行：

| 字段 | 内容 |
|---|---|
| Feature ID | `Dxx-NNN` |
| 测试项 | 一句话行为断言 |
| 预期结果 | HTTP/状态/事件/日志的确定结果 |
| 命令 | `pytest ...` 或 fake harness 命令 |
| 工件 | 脱敏日志/SSE/fixture 路径 |
| 生命周期插入点 | Account/Instance/Pool-Router/Executor/Event Sink |
| 证据等级 | 离线 fixture / fake CDP / 受控真实（后置） |

最低验收：

- P3/P2：无 lease 断言；`active_requests` finally 释放；候选含 SLEEPING/UNSTARTED；429 同号重试→切换→503；保存后切换重试；rebind 先 `RETIRING`/停在途/有界等待/关旧 Page/Context 再建新页；drain 超时强制关闭；page prune 关闭所有非活跃 target；drain 只在旧 Page/Context 上且期间不建新页；watchdog 保留；CDP 只有 WS ping；测试禁止 blue-green 双页。
- P5/PG：F-01 functionCall 缺 thoughtSignature 兜底；F-02 首事件前真实 HTTP 4xx/5xx；F-03 finish-only/工具流终止兜底；F-04 functionResponse/functionCall 强制 Struct；403 tier 不足体含 `model/required_tier/current_tier`；seed 仅 Gemini；`[toolshape]` 脱敏。F-05/06/07 后置。
- P6/P7：24h fake clock；build-only 回写；日志 SSE/1/3/7 天；client/attempt 分母；token 汇总不含 countTokens；`finish_reasons` 先进 stats API，UI 后置。

### 5.1 目标工件路径（由迁移 worktree 创建/验证；主工作区不复制实现 fixture）

- `config/config.example.yaml`：本页 §3 的示例（含 `quota_scope`、`security.api_allow_anonymous`、`security.web_password`、rotator/paid_model_rules/tier_quotas/account_presets、`hash_include_code`，600 超时，无 idle 键）。
- `config/config.schema.json`：§3 的机器校验 schema（含 D-07 的 rotator 校验、D-14 的鉴权分拆）。
- `tests/fixtures/events/event.sample.json`：§4 的事件样例。
- `tests/fixtures/pg/f01_missing_signature.json`
- `tests/fixtures/pg/f02_prestream_400.json`
- `tests/fixtures/pg/f03_finish_only.bin`
- `tests/fixtures/pg/f04_string_response.json`
- `docs/dev/rebuild/acceptance/README.md`：每片验收工件目录规范。

主工作区只保留文档契约；上述 config/tests fixture 由迁移 worktree 基于 99c38ba 创建并验证，不得从旧 worktree 直接复制为实现工件。

## 6. Gate 0.5 退出清单

评审：主工作区文档已按 99c38ba 重审（08-08/08-09）；Gate 0.5 未在迁移 worktree 基于 99c38ba 重新验证，故下列项保持未勾选，需由迁移 agent 验证后回填。

- [ ] 本页五节均通过评审并绑定 feature ID；需独立 reviewer 基于 99c38ba 复核。
- [ ] `06-test-disposition.csv` 覆盖 `tests/**/*.py`：主 rebuild 80/80、99c38ba 28/28，且无 `pending`；并补 8 个新端点的路由级/行为级测试（28/28 只是文件覆盖，不得当作行为通过）。
- [ ] 状态表能覆盖 sleep/wake/rebind/429/403/503 的转换测试（无 idle 自动回收；D-05 有界 rebind；D-10/D-11 drain/prune）。
- [ ] config/event schema 有示例与离线 fixture 路径，并在迁移 worktree 实际创建；`quota_scope`、`api_allow_anonymous`、`web_password`、`hash_include_code` 均有校验用例。
- [ ] UI 验收：现有 `SettingsView`/`ApiKeyManagerCard` 中 `quota_scope`、API 免密开关、管理页密码空=免密均“可见且可保存”；不新增 IP/CIDR/本机-局域网模式；不重构 Web 布局/导航。
- [ ] 每片 acceptance 模板已用于 P9-pre 的首个切片。

### 6.1 P4 阶段前期探索（不属于 Gate 0.5，当前不执行）

到 P4 Build 实现阶段启动时，先按 `08-10-build-surface-incident-20261028.md` §4 做前期探索和可行性重试；再实现，最后验证 400 分类、错误透传、native/signer 分别测试、9998 无循环重连。当前没有进入 P4，不要求补证据或签字。
