# verify_required 状态与前端验证 UI 方案（研究，不施工）

> 本文只做方案研究与接入点盘点，不修改 `src/`、`web/`、`tests/`、`defaults/`。所有前端结论均来自当前真实代码，已附 `file:line`。ASCII 线框图按真实组件结构绘制，不使用截图冒充。

## 1. 现状结论（先事实，后方案）

### 1.1 当前账号状态链路

```text
上游 403 / 报错 body
  -> domain/errors.py: is_account_verification_required_message()
  -> AuthError.verification_required = true
  -> routing_pipeline.py: 记录 verification_required 事件并切号
  -> account_orchestrator.py: verification_required -> rotator.record_session_expired()
  -> AccountStats.session_expired = true
  -> routes_accounts.py: AccountResponse.session_expired = true
  -> 前端 accountStatus.ts: expired === true || session_expired === true -> 'expired'
  -> AccountRow.vue: 徽章“登录态失效”；按钮“恢复”
```

关键事实：

| 事实 | 证据 |
|---|---|
| 后端已经能识别 verify-account 报错 | `src/aistudio_api/domain/errors.py:12-27`、`:37-39` |
| 路由层已经单独记录 `verification_required` 事件 | `src/aistudio_api/application/routing_pipeline.py:939-958` |
| 但运行时把 verify 事件折叠进 `session_expired` | `src/aistudio_api/application/account_orchestrator.py:72-82` |
| 前端把 `expired` 与 `session_expired` 合并为同一个 `expired` 状态 | `web/src/utils/accountStatus.ts:33-35` |
| 前端只显示“登录态失效”，没有“需扫码验证” | `web/src/components/accounts/AccountRow.vue:318-325` |
| 前端“恢复”只做 refresh + clearCooldown | `web/src/components/accounts/AccountTable.vue:312-315`、`web/src/stores/accounts.ts:436-468` |
| 后端保活明确不探测 verify-account 锁 | `src/aistudio_api/application/account_keepalive.py:203-227` |

结论：**新增 `verify_required` 不是从零造状态，而是把已经存在的 `verification_required` 事件从 `session_expired` 里拆出来。** 这是最小、最符合现有架构的切入点。

### 1.2 当前 Account 模型

`web/src/types/accounts.ts:3-24`：

```ts
export interface Account {
  id: string
  name: string
  email: string | null
  created_at: string
  last_used?: string | null
  auth_user: string
  cookie_id?: string | null
  tier?: string
  tier_checked_at?: string | null
  expired?: boolean
  duplicate_of?: string | null
  enabled: boolean
  group: string
  session_expired?: boolean
  auth_cooldown?: number
  is_available?: boolean
  rebind_required?: boolean
  previous_active_account_id?: string | null
  active_account_id?: string | null
  target_account?: Account | null
}
```

没有 `verify_required` 字段。

### 1.3 当前状态优先级

`web/src/utils/accountStatus.ts:4-11`：

```ts
export type AccountStatusKey =
  | 'disabled'
  | 'expired'
  | 'cooldown'
  | 'instance'
  | 'active'
  | 'quota'
  | 'ready'
```

`web/src/utils/accountStatus.ts:25-50`：

```text
disabled
  -> expired (expired === true || session_expired === true)
  -> cooldown (auth_cooldown > 0)
  -> instance (boundInstanceFor)
  -> active
  -> quota (is_available === false)
  -> ready
```

### 1.4 当前筛选与徽章

| 位置 | 现状 |
|---|---|
| `web/src/components/accounts/AccountTable.vue:46` | `statusFilter` 只有 `all / ready / limited / cooldown / expired` |
| `web/src/components/accounts/AccountTable.vue:48-59` | `expired` 映射到 `status.key === 'expired'`；没有 verify 分支 |
| `web/src/components/accounts/AccountTable.vue:419-461` | 筛选 pills：全部 / 就绪 / 配额耗尽 / 鉴权冷却 / 登录态失效 |
| `web/src/components/accounts/AccountRow.vue:311-360` | 状态 pill 分支：已停用 / 登录态失效 / 鉴权冷却中 / 实例 / 当前激活 / 配额耗尽 / 就绪 |
| `web/src/components/accounts/AccountRow.vue:365-375` | 仅 `isExpired` 显示“恢复”按钮 |
| `web/src/views/AccountsView.vue:343-375` | 只有“分组”筛选，没有状态筛选；状态筛选在 AccountTable 内部 |

### 1.5 当前可复用的“恢复”动作

`web/src/components/accounts/AccountRow.vue:90-92`：

```ts
const isExpired = computed(() => {
  return props.account.expired === true || props.account.session_expired === true
})
```

`web/src/components/accounts/AccountTable.vue:312-320`：

```ts
// 针对登录态失效或受阻账号执行重新验证（刷新清除 expired 后清除 session_expired/冷却）
async function handleReverifyAccount(acc: AccountWithStats) {
  await accountsStore.recoverAccount(acc.id)
}
```

`web/src/stores/accounts.ts:436-468`：

```ts
async function recoverAccount(id: string) {
  const res = await accountsApi.refresh({ account_ids: [id], refresh_email: true })
  if (result?.expired === true) { toast.warning('登录态仍然失效，请重新导入 Cookies 后再恢复'); ... }
  await systemApi.clearCooldown({ account_id: id })
  toast.success('账号登录态已恢复')
}
```

问题：这套动作对 verify-account 无效。verify-account 的会话可能仍然有效，但上游要求移动设备验证；refresh 不能清掉锁，clearCooldown 也不应清掉 verify 锁。

### 1.6 当前 API 面

| 能力 | 前端调用 | 后端路由 |
|---|---|---|
| 列表 | `web/src/api/accounts.ts:21-23` | `src/aistudio_api/api/routes_accounts.py:460-471` |
| 刷新 | `web/src/api/accounts.ts:131-136` | `routes_accounts.py:488-489` |
| 清冷却 | `web/src/api/system.ts:38-42` | `src/aistudio_api/api/routes_system.py:470-496` |
| 启停 | `web/src/api/accounts.ts:83-88` | `routes_accounts.py:1021-1022` |

没有 verify 相关 API。

## 2. 建议的 verify_required 状态机

### 2.1 账号级状态

在“账号状态”维度新增一个持久化/可观测状态，与“登录态失效”严格区分：

```text
                   上游返回 verify-account 标记
normal ───────────────────────────────────────► verify_required
  ▲                                                   │
  │                                                   │ 用户点击「验证」
  │                                                   ▼
  │                                              verifying
  │                                                   │
  │        状态查询返回 verified / VerifyUser=1        │
  └───────────────────────────────────────────────────┤
                                                      │
                                                      ├──► verify_failed（超时/未通过/二维码过期）
                                                      │        │
                                                      │        └── 重试 -> verifying
                                                      │
                                                      ├──► verify_error（网络/401/上游异常）
                                                      │        │
                                                      │        └── 重试 -> verifying
                                                      │
                                                      └──► expired（真实会话失效：401 SESSION_COOKIE_INVALID）
```

说明：

- `verify_required` 表示“会话仍可能有效，但上游要求完成 Google 移动设备验证”。
- `expired` 表示“Cookie 真正失效，必须重新导入”。
- `verifying` 是 UI 瞬态，不持久化。
- `verify_failed` / `verify_error` 是弹窗内瞬态，不写账号主状态；账号仍保持 `verify_required`，直到成功或用户取消。
- `disabled` 优先级最高；用户手动停用时不显示验证按钮。

### 2.2 前端状态优先级（建议）

```text
disabled
  -> expired（真实登录失效）
  -> verify（verify_required === true）
  -> cooldown
  -> instance
  -> active
  -> quota
  -> ready
```

理由：`verify_required` 与 `expired` 是两种不同恢复路径；先把真实失效排前面，避免把“需验证”误显示成“登录态失效”。如果后端确认 `verify_required` 时 `expired` 一定为 false，则顺序不会冲突；仍然建议显式排前。

### 2.3 后端字段建议（最小改动）

| 层 | 字段/改动 | 说明 |
|---|---|---|
| `AccountMeta`（`account_store.py:322-345`） | 新增 `verify_required: bool = False` | 持久化到 `meta.json`，跨重启保留 |
| `AccountStats`（`account_rotator.py:153-171`） | 新增 `verify_required: bool = False`、`last_verify_required: float = 0.0` | 运行时状态；与 `session_expired` 分离 |
| `account_orchestrator.py:72-82` | `verification_required` 改为写 `record_verify_required()`，不再调用 `record_session_expired()` | 不再污染兄弟号；不连坐 |
| `routing_pipeline.py:939-958` | 保持单独 `verification_required` 事件 | 已有，只需改落库目标 |
| `AccountResponse`（`routes_accounts.py:142-164`） | 新增 `verify_required: bool = False` | API 返回 |
| `_account_status`（`routes_accounts.py:102-139`） | 增加 `verify_required`，并让 `is_available` 在 verify 时为 false | 调度准入/前端状态一致 |
| `to_account_response`（`routes_accounts.py:221-261`） | 透传 `verify_required` | 列表/激活/导入响应都带上 |
| `ui_sandbox.py:272-283` | `_AccountSeed` 增加 `verify_required`，并加种子账号 | UI Debug 验收必需 |
| 迁移 | `AccountMeta.from_dict` 默认 false | 老 `meta.json` 无需迁移脚本 |

注意：不要把 `verify_required` 直接映射成 `expired=True`。现有 `account_keepalive.py:203-227` 明确把 verify-account 当作“不可自动探活”的锁；如果继续用 `expired` 表示，会丢失“可以用扫码恢复”的语义。

### 2.4 清除时机

| 事件 | 动作 |
|---|---|
| `GetUserVerificationStatus` 返回 2（verified） | 清除 `verify_required`，`is_available` 恢复 |
| `VerifyUser` 返回 1（passed） | 清除 `verify_required`，记录验证成功时间 |
| 用户重新导入 Cookies 且 refresh 成功 | 清除 `verify_required`（可选，需用户拍板） |
| 用户手动清除锁定 | 只清 429/冷却，不清 verify；verify 需要单独“已验证”证据 |
| 真实 401 / `SESSION_COOKIE_INVALID` | 标记 `expired`，不是 `verify_required` |

## 3. 后端验证逻辑：两种可选架构

### 方案 A（推荐先做）：外部验证页 + 服务端状态查询

```text
前端「验证」按钮
  -> POST /api/accounts/{id}/verify/session
     后端：只创建一个验证会话记录（不启动浏览器）
     返回：{ mode:"external", verify_url:"https://aistudio.google.com/verify-account", expires_at }
  -> 前端打开新标签页/提示用户在该页完成扫码
  -> 前端每 2~3s 调 GET /api/accounts/{id}/verify/status
     后端：用该账号已存 Cookies 调 GetUserVerificationStatus
     返回：{ status:"required"|"verified"|"notRequired"|"expired"|"error", checked_at }
  -> status=verified => 清除 verify_required，弹窗成功
```

优点：

- 不需要后端启动真实浏览器，不触发新的 BotGuard/WAA 冷启动。
- 二维码由 Google 页面自己生成，行为与用户手动操作一致。
- 后端只做只读状态查询，风险最低。

缺点：

- 用户需要手动打开/切换标签页；二维码不在本系统弹窗内。
- `GetUserVerificationStatus` 是否会在扫码后立即变 2，取决于 Google 的服务端时序（未动态验证；见 REPORT 的 U 项）。
- 如果状态查询一直返回 required，需要提示用户“以 AI Studio 页面结果为准”，或让用户点“我已验证”后由后端再查一次。

### 方案 B（高风险，需用户明确拍板）：后端浏览器取二维码

```text
前端「验证」按钮
  -> POST /api/accounts/{id}/verify/session
     后端：在受控浏览器中注入该账号 Cookies，打开 verify-account
     等待 reCAPTCHA bframe 出现 QR
     读取 #rc-mobile-verification-button href / canvas.title 短码
     可选：截图 canvas 或由短码重新生成 QR
     返回：{ mode:"qr", qr_image:"data:image/png;base64,...", shortcode, expires_at }
  -> 前端展示二维码并轮询状态
```

可行性：

- 技术上可复用用户脚本的 DOM 读取逻辑（`#rc-mobile-verification-button`、`canvas.recaptchaJavascriptChallengeQrCanvas`、`.recaptchaJavascriptChallengeQrFallbackText`）。
- 但必须在浏览器上下文内，且会触发 reCAPTCHA Enterprise + WAA/BotGuard；每次冷启动都可能触发风控。
- 需要处理：会话有效、页面域、BotGuard 初始化、reCAPTCHA challenge 可能不是 QR（可能直接通过或给其他挑战）、二维码过期、bframe 跨域。
- 不建议在常规请求路径中做；应作为独立、显式、低频的运维动作，并且需要用户授权。

结论：**方案 A 是研究后的低风险建议；方案 B 只能作为“用户明确要求自动化取码”时的后续实验，不应默认实现。**

## 4. 前端接入点清单（最小改动面）

### 4.1 类型与状态

| 文件 | 改动 |
|---|---|
| `web/src/types/accounts.ts:3-24` | `Account` 增加 `verify_required?: boolean` |
| `web/src/utils/accountStatus.ts:4-11` | `AccountStatusKey` 增加 `'verify'` |
| `web/src/utils/accountStatus.ts:25-50` | 在 `expired` 之后、`cooldown` 之前加 `verify_required` 分支 |
| `web/src/components/accounts/AccountTable.vue:46` | `statusFilter` 增加 `'verify'` |
| `web/src/components/accounts/AccountTable.vue:48-59` | `matchesStatusFilter` 增加 `status.key === 'verify'` |
| `web/src/components/accounts/AccountTable.vue:419-461` | 增加“需验证”pill |
| `web/src/components/accounts/AccountRow.vue:311-360` | 增加 `status.key === 'verify'` 徽章 |
| `web/src/components/accounts/AccountRow.vue:363-441` | 增加“验证”按钮（仅 verify 且 enabled 且未 expired） |
| `web/src/views/AccountsView.vue:439-467` | 挂载 `VerifyAccountModal`（与现有 modal 同级） |

### 4.2 Store 与 API

| 文件 | 改动 |
|---|---|
| `web/src/stores/accounts.ts` | 增加 `verifySession` 状态、`startVerify(id)`、`fetchVerifyStatus(id)`、`stopVerify()` |
| `web/src/api/accounts.ts` | 增加 `startVerifySession(id)`、`getVerifyStatus(id)`、`cancelVerifySession(id)` |
| `web/src/components/modals/VerifyAccountModal.vue` | 新建弹窗组件 |
| `web/src/types/accounts.ts` | 增加 `VerifySessionResponse`、`VerifyStatusResponse` |

建议的类型：

```ts
export type VerifyMode = 'external' | 'qr'
export type VerifyStatus = 'required' | 'verified' | 'notRequired' | 'expired' | 'error'

export interface VerifySessionResponse {
  mode: VerifyMode
  verify_url?: string
  qr_image?: string
  shortcode?: string
  expires_at: string
  poll_interval_ms?: number
}

export interface VerifyStatusResponse {
  status: VerifyStatus
  checked_at: string
  message?: string
}
```

### 4.3 后端 API 建议

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/accounts/{id}/verify/session` | 创建验证会话；方案 A 只返回外部 URL，方案 B 返回二维码 |
| `GET` | `/api/accounts/{id}/verify/status` | 用已存 Cookies 调 `GetUserVerificationStatus`，返回 mapped status |
| `POST` | `/api/accounts/{id}/verify/cancel` | 取消会话；停止后端轮询/浏览器上下文（方案 B 必需） |
| `POST` | `/api/accounts/{id}/verify/complete` | 可选：用户点“我已验证”后强制复查并清锁 |

注意：不要把 `clearCooldown` 复用为 verify 完成接口。两者语义不同；`routes_system.py:470-496` 只应清 429/冷却。

### 4.4 UI Debug 沙盒

| 文件 | 改动 |
|---|---|
| `src/aistudio_api/api/ui_sandbox.py:272-283` | `_AccountSeed` 增加 `verify_required` |
| `src/aistudio_api/api/ui_sandbox.py:1688-1760`（flags 读取/返回） | 返回 `verify_required` |
| `src/aistudio_api/api/ui_sandbox.py:1946-1953`（种子） | 增加一个 `verify_required=True` 的 Free 账号 |
| `src/aistudio_api/api/ui_sandbox.py:980`（设置 flags） | 支持设置 `verify_required` |

否则在 `AISTUDIO_UI_DEBUG=1` 沙盒里无法验收新徽章、筛选和弹窗。

## 5. 「验证」按钮交互流

### 5.1 打开弹窗

```text
点击 AccountRow 的「验证」
  -> accountsStore.startVerify(account.id)
  -> POST /api/accounts/{id}/verify/session
  -> loading: 弹窗打开，QR 区域 skeleton，按钮禁用
  -> 成功:
       mode=external -> 显示“在浏览器打开验证页”+“检查状态”
       mode=qr       -> 显示二维码 + 倒计时 + “我已扫码，检查状态”
  -> 失败:
       401 -> “登录态已失效，请重新导入 Cookies”（可选标记 expired）
       其他 -> “无法创建验证会话，请重试”
```

### 5.2 轮询

```text
弹窗打开且 mode=qr / external 时：
  每 poll_interval_ms（默认 2500ms，后端可下发）
  -> GET /api/accounts/{id}/verify/status
  -> verified     : 停止轮询，成功态，清 verify_required，刷新列表
  -> notRequired  : 停止轮询，提示“无需验证”，清 verify_required
  -> required     : 继续轮询，直到 expires_at
  -> expired      : 停止轮询，提示“二维码/会话已过期”，显示“刷新二维码”
  -> error        : 连续 3 次失败后停止，提示“状态查询失败”，保留“重试”
```

约束：

- 同一账号同一时间只允许一个验证会话；重复点击只聚焦已有弹窗。
- 关闭弹窗/取消必须 `AbortController.abort()` 并调用 cancel 接口（方案 B 必需）。
- 页面隐藏（`visibilitychange`）时降低轮询频率或暂停；恢复可见时立即查一次。
- 不在全局 8s 轮询（`AccountsView.vue:81` 的 `usePolling(loadData, 8000)`）里夹带 verify 状态查询，避免放大请求。

### 5.3 loading / 成功 / 失败 / 取消

| 状态 | UI |
|---|---|
| loading | QR 区 skeleton + “正在获取二维码…”；按钮禁用；可取消 |
| ready | 二维码/外部按钮 + 倒计时 + “等待扫码”；“刷新二维码”“检查状态”“取消”可用 |
| verifying | 二维码上覆盖半透明 spinner + “正在确认验证结果…”；禁止重复点击“检查状态” |
| passed | 绿色 check + “验证成功，账号已恢复调度”；1.5s 后自动关闭或保留“完成” |
| failed | 红色/琥珀色提示 + “未检测到验证通过，请重试或更换设备”；“重试”“关闭” |
| expired | 琥珀色提示 + “二维码已过期”；“刷新二维码”“关闭” |
| error | 红色提示 + 具体错误（401/网络/上游）；“重试”“关闭” |
| cancelled | 关闭弹窗；toast.info(“已取消验证”)；账号状态不变 |

### 5.4 与现有“恢复”按钮的关系

| 状态 | 显示按钮 | 动作 |
|---|---|---|
| `expired`（真实登录失效） | “恢复” | 现有 `recoverAccount`：refresh + clearCooldown |
| `verify`（需扫码） | “验证” | 新流程：验证会话 + 状态轮询 |
| `cooldown` | “清除锁定” | 现有 clearCooldown |
| `quota` | 无专用按钮 | 现有模型配额网格/清冷却 |

不要把“验证”按钮接到 `recoverAccount`，否则会误导用户以为刷新就能解除 verify-account。

## 6. ASCII 线框图

### 6.1 账号管理页全景（新增位置用 `[NEW]` 标注）

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 账号管理                                             [重置锁定] [油猴抓取脚本] [导入 Cookies] │
├──────────────────────────────────────────────────────────────────────────────┤
│ StatsStrip:  总账号 47 | 可用 39 | 失效 2 | 需验证 5 [NEW] | 429 锁定 1        │
├──────────────────────────────────────────────────────────────────────────────┤
│ KeepaliveBar: 上次保活 12:30 | 下次 13:30 | [立即保活]                        │
├──────────────────────────────────────────────────────────────────────────────┤
│ 账号预设  [白班池] [低延迟] [全 Free]                         [保存当前方案]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ 实例池  [槽位 #1 Free active] [槽位 #2 Free sleeping] ...                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ 分组:  (全部 47)  (default 30)  (备用 17)                                     │
│                                                                              │
│ 搜索 [ 账号名/邮箱/u/0... ]   状态: [全部] [就绪] [配额耗尽] [鉴权冷却]       │
│                                      [登录态失效] [需验证 NEW]                │
├──────────────────────────────────────────────────────────────────────────────┤
│ ▼ cookie_*** (3 个账号)                                    [展开全部] [收起] │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ [开关] u/0 [Free] [default]  a***@***  ID: acc_***                       │ │
│ │ 今日 12 · 累计 345  [成功 300] [429: 2]   [需扫码验证 NEW]               │ │
│ │                                    [验证 NEW] [换槽] [刷新] [删除]        │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ [开关] u/1 [Pro]  [default]  b***@***  ID: acc_***                       │ │
│ │ 今日 0 · 累计 88  [成功 88] [429: 0]    [登录态失效]                     │ │
│ │                                    [恢复] [换槽] [刷新] [删除]            │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ [开关] u/2 [Free] [备用]  c***@***  ID: acc_***                          │ │
│ │ 今日 3 · 累计 20  [成功 20] [429: 0]    [就绪]                           │ │
│ │                                    [激活] [刷新] [删除]                   │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

对应真实结构：

- 顶部动作栏：`AccountsView.vue:241-279`
- StatsStrip：`AccountsView.vue:281-282`
- KeepaliveBar：`AccountsView.vue:284-285`
- 预设：`AccountsView.vue:287-335`
- 实例池：`AccountsView.vue:337-341`
- 分组 tabs：`AccountsView.vue:343-375`
- 状态筛选 pills：`AccountTable.vue:405-462`
- 账号行：`AccountRow.vue:151-452`
- 新徽章插在 `AccountRow.vue:311-360` 的状态 pill 分支
- 新按钮插在 `AccountRow.vue:363-441` 的按钮组
- 新筛选插在 `AccountTable.vue:419-461`

### 6.2 「扫码验证」弹窗放大图（方案 B：能在弹窗内展示二维码）

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 扫码验证账号                                                   [×]   │
│ 账号: a***@*** · u/0 · Free · ID: acc_***                            │
├──────────────────────────────────────────────────────────────────────┤
│ 状态: ● 等待扫码        倒计时 04:32        轮询中 ⟳                │
│                                                                      │
│                  ┌────────────────────────────┐                      │
│                  │                            │                      │
│                  │                            │                      │
│                  │          [ QR CODE ]       │                      │
│                  │                            │                      │
│                  │                            │                      │
│                  └────────────────────────────┘                      │
│                                                                      │
│  用手机相机扫描二维码，并按手机上的提示完成验证。                    │
│  二维码由 Google reCAPTCHA 提供；本弹窗只负责展示与状态回传。        │
│                                                                      │
│  ▸ 短码（可选，默认折叠）  ABCD-EFGH  [复制]                         │
│                                                                      │
│  ⓘ 验证成功后账号会自动恢复调度；二维码过期后请点“刷新二维码”。     │
├──────────────────────────────────────────────────────────────────────┤
│ [刷新二维码]              [我已扫码，检查状态]              [取消]   │
└──────────────────────────────────────────────────────────────────────┘
```

弹窗状态变体：

```text
loading:
┌──────────────────────────────────────────────┐
│ 扫码验证账号                          [×]   │
│ 正在获取二维码…                              │
│   ┌──────────────────────────────┐           │
│   │        [ skeleton QR ]       │           │
│   └──────────────────────────────┘           │
│                              [取消]           │
└──────────────────────────────────────────────┘

verifying:
┌──────────────────────────────────────────────┐
│ 状态: ◌ 正在确认验证结果…      [ spinner ]  │
│   ┌──────────────────────────────┐           │
│   │   [ QR CODE + 半透明遮罩 ]   │           │
│   └──────────────────────────────┘           │
│                              [取消]           │
└──────────────────────────────────────────────┘

passed:
┌──────────────────────────────────────────────┐
│ ✓ 验证成功                                    │
│ 账号已验证，已恢复调度。                      │
│                                    [完成]     │
└──────────────────────────────────────────────┘

failed / expired:
┌──────────────────────────────────────────────┐
│ ! 未检测到验证通过 / 二维码已过期             │
│ 请刷新二维码后重试，或更换一台手机。          │
│ [刷新二维码] [重试]                    [关闭] │
└──────────────────────────────────────────────┘
```

### 6.3 「扫码验证」弹窗（方案 A：外部验证页，推荐先做）

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 验证账号                                                       [×]   │
│ 账号: a***@*** · u/0 · Free · ID: acc_***                            │
├──────────────────────────────────────────────────────────────────────┤
│ 该账号被 Google 要求完成移动设备验证。                                │
│                                                                      │
│ 二维码只能在浏览器内的 AI Studio 验证页生成，本系统不会在后台         │
│ 自动拉起浏览器。请点击下方按钮，在新标签页完成扫码。                  │
│                                                                      │
│ 状态: ○ 等待验证        检查中 ⟳      最近检查: 12:30:05             │
│                                                                      │
│ ① 打开验证页  ② 手机扫码  ③ 回到本弹窗点“检查状态”                  │
├──────────────────────────────────────────────────────────────────────┤
│ [打开验证页]              [检查状态]                        [取消]   │
└──────────────────────────────────────────────────────────────────────┘
```

方案 A 的成功态：

```text
┌──────────────────────────────────────────────┐
│ ✓ 验证成功                                    │
│ 账号已验证，已恢复调度。                      │
│                                    [完成]     │
└──────────────────────────────────────────────┘
```

方案 A 的失败态：

```text
┌──────────────────────────────────────────────┐
│ ! 尚未检测到验证通过                          │
│ 请确认已在 AI Studio 验证页完成扫码。         │
│ [重新检查] [重新打开验证页]            [关闭] │
└──────────────────────────────────────────────┘
```

## 7. 研究结论 / 实现建议 / 必须拍板

### 7.1 已确认的研究结论

1. 当前前端没有 `verify_required` 状态；`verification_required` 被后端折叠进 `session_expired`（`account_orchestrator.py:72-82`），前端再折叠进 `expired`（`accountStatus.ts:33-35`）。
2. 当前“恢复”按钮不能解决 verify-account；它只 refresh + clearCooldown（`stores/accounts.ts:436-468`）。
3. 新增独立状态在现有架构上可行，最小改动是“AccountMeta + AccountStats + AccountResponse + accountStatus + 徽章/筛选/按钮/弹窗”。
4. 二维码本身不能由 AI Studio RPC 直接返回；它是 reCAPTCHA bframe 内的 Canvas/短码（见 `REPORT.md` 与 `EVIDENCE.md`）。
5. 扫码通过的权威判据是 `VerifyUser` 返回 1；`GetUserVerificationStatus` 是前置/粗查状态，不是挑战内的实时轮询器。

### 7.2 实现建议（按风险从低到高）

| 阶段 | 内容 | 风险 |
|---|---|---|
| P1 | 拆出 `verify_required` 字段与徽章/筛选/按钮；不自动取码 | 低 |
| P2 | 方案 A：外部验证页 + 服务端 `GetUserVerificationStatus` 查询 | 低-中 |
| P3 | 可选“我已验证”按钮，强制复查并清锁 | 中 |
| P4 | 方案 B：受控浏览器取二维码/截图 | 高，需单独授权与实验 |

### 7.3 必须用户拍板

1. **是否允许后端自动拉起真实浏览器取二维码？** 这会触发 BotGuard/WAA/reCAPTCHA，可能消耗验证次数并触发风控；默认建议不做。
2. **验证弹窗是外部跳转还是内嵌二维码？** 外部跳转风险最低；内嵌二维码需要方案 B。
3. **是否自动轮询状态？** 自动轮询体验好，但会增加带凭据请求；建议 2.5s、仅在弹窗打开时、可暂停。
4. **`verify_required` 是否持久化到 `meta.json`？** 建议持久化，跨重启保留；需要确认是否接受状态字段扩展。
5. **verify 账号是否保持 `enabled=true` 但 `is_available=false`？** 建议保持启用但不可调度，避免用户误以为被删除；也可选择自动停用。
6. **是否允许“重新导入 Cookies 即清 verify 锁”？** 建议只在 refresh 成功且 `GetUserVerificationStatus` 返回 2 时清，不凭导入动作直接清。
7. **是否展示/复制短码？** 短码是一次性挑战凭证；建议默认折叠、只允许复制、不写日志。
8. **是否允许批量验证/自动修复？** 建议先不做，避免放大风控。
9. **是否支持 Pro 账号的验证入口？** 当前用户明确禁止使用两个 Pro 主力号；UI 可以先对所有 tier 显示状态，但操作需用户确认。
10. **是否接入第三方中转页（`qr.cuberelay.workers.dev`）？** 不建议；会把短码发给第三方，且引入外部依赖。

## 8. 验收与测试建议（未施工，仅计划）

### 8.1 后端单测

- `verification_required` 事件只写 `verify_required`，不写 `session_expired`，不连坐兄弟号。
- `AccountResponse.verify_required` 在 list/active/toggle/refresh 响应中一致。
- `is_available` 在 verify 时为 false；在 status=verified 清除后恢复 true。
- 真实 `SESSION_COOKIE_INVALID` 仍映射 `expired`，不能误判为 verify。
- `clearCooldown` 不清 `verify_required`。
- `ui_sandbox` 能返回/设置 `verify_required`。

### 8.2 前端沙盒

- 启动：`uv run python main.py server --port <random> --ui-debug`（遵循 AGENT.md 4.6）。
- 多分辨率：`1440x900` / `1024x900` / `768x900`。
- 断言：
  - 筛选“需验证”只显示 verify 账号。
  - 徽章文案/颜色与“登录态失效”明显不同。
  - 点击“验证”打开弹窗；loading → ready → verifying → passed/failed/expired 状态可切换。
  - 取消/关闭后轮询停止（可用 sandbox 计数或日志验证）。
  - 1440/1024/768 下二维码区域不溢出、不遮挡按钮。
- Reviewer 需归档 1~2 张完整页面截图到 `reviews/assets/` 并在 `REV-*.md` 引用。

### 8.3 不应做的事

- 不为跑 UI 启动真实服务/真实浏览器（AGENT.md 4.6）。
- 不在常规请求路径里自动执行 verify-account 挑战。
- 不把短码、Cookie、Authorization、WAA snapshot 写入日志。
- 不用 `session_expired` 继续冒充 verify 状态。
