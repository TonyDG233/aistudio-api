# 出口管理器控制面契约（app ↔ exit-manager + fanout 机器接口）

本文件固化 `aistudio-api`（app 侧）、宿主 `exit-manager`（v2）与 fanout 机器接口之间的最小 HTTP 契约，是 `data/tools/exit-manager/DEPLOY.md` §5 的仓库内镜像。实现事实来源：

- 管理器 v2：`data/tools/exit-manager/exit_manager/{config,state,manager,server,health,replenish}.py`
- app 客户端：`src/aistudio_api/infrastructure/exit_pool/resolver.py`
- app 配置：`src/aistudio_api/infrastructure/exit_pool/config.py`
- 管理页只读字段：`src/aistudio_api/api/routes_system.py`（`GET /config`）
- fanout 机器接口：fanout fork `279fd9c`（`machine.go` / `main.go` / `README.md`），详见 `REV-2.1.5.12`

任何字段/语义变更必须同时更新本文件与 `DEPLOY.md`。旧契约中的 `GET /v1/assign` 主入口、503 `no_healthy_exit`、`ttl_s=300`、`granted/category` 等已作废；见 §10。

---

## 1. 通用约定

- **Base URL**：`AISTUDIO_EXIT_ENDPOINT`，仅接受 `http://` / `https://` 且主机非空；非法值使整个出口池保持关闭（`get_exit_resolver() is None`）。
- **鉴权**：所有 `/v1/*` 请求携带 `Authorization: Bearer <AISTUDIO_EXIT_TOKEN>`；token 必须与管理器 `config.json` 的 `token` 完全一致（前后空白会被裁剪，仍不一致即 401）。管理器用 `hmac.compare_digest` 做常量时间比较。
- **来源限制**：管理器只接受 `security.allowed_client_cidrs` 内的源地址（默认 `127.0.0.0/8`、`::1/128`、`172.17.0.0/16`），否则 403。
- **监听面**：管理器仅允许 `127.0.0.1` / `::1` / `localhost` / `172.17.0.1`；拒绝公网绑定。
- **方法**：只读接口只接受 `GET`；租约/事件/恢复只接受 `POST`；方法不符返回 405 + `Allow`。
- **超时**：app 侧控制面超时由 `AISTUDIO_EXIT_HTTP_TIMEOUT` 控制（默认 2s）。
- **失败非致命（fail-closed）**：任何超时 / 非 200 / 非 JSON / 非法载荷都只在 app 侧降级，**绝不回退全局 `AISTUDIO_PROXY`**。管理器 `429 no_capacity` 是**延迟重试**，不是 fail-closed 放弃，也不是 503。
- **远端部署**：`AISTUDIO_EXIT_ENDPOINT` 指向非 loopback / 非 docker 桥地址时必须使用 `https://`，Bearer token 不得以明文 HTTP 跨主机传输。
- **共享 secret**：`account` 为 `HMAC-SHA256(AISTUDIO_EXIT_HMAC_KEY, account_id)` 的十六进制；管理器只保存 HMAC，不持有盐、不读账号文件。

---

## 2. `GET /v1/status`（只读观测）

### 2.1 请求

```http
GET /v1/status
Authorization: Bearer <token>
```

无查询参数、无请求体。

### 2.2 成功响应（200，管理器 v2 全量）

```json
{
  "manager_ok": true,
  "exits": {
    "total": 6,
    "healthy": 4,
    "quarantined": 0,
    "disabled": 0,
    "cooling": 1
  },
  "cooldown_count": 1,
  "lanes": {
    "paid": {"healthy": 2, "target": 3, "cap": 3, "window_remaining": 58, "daily_remaining": 790},
    "free": {"healthy": 1, "target": 3, "cap": 6, "window_remaining": 48, "daily_remaining": 2380},
    "free_overflow": {"healthy": 1, "target": 1, "cap": 1, "window_remaining": 49, "daily_remaining": 2390}
  },
  "bound_accounts": 12,
  "last_switch_at": 1764000000,
  "last_replenish_at": 1764000100,
  "maintenance_queue": 0
}
```

| 字段 | 类型 | 语义 |
|---|---|---|
| `manager_ok` | bool | 管理器已完成状态汇总；app 仅在严格 `true` 时视为在线 |
| `exits.total` | int | 配置中的出口总数 |
| `exits.healthy` | int | 当前可分配的健康出口数 |
| `exits.quarantined` | int | 被隔离、等待人工 `recover` 的出口数 |
| `exits.disabled` | int | 配置 `disabled: true`、永不分配的出口数 |
| `exits.cooling` | int | verify 连坐烧毁、等待冷却到期的出口数 |
| `cooldown_count` | int | 等价于 `exits.cooling`（兼容/显式字段） |
| `lanes.<lane>.healthy` | int | 该 lane 当前 healthy 数 |
| `lanes.<lane>.target` / `cap` | int | paid `3/3`、free `3/6`、free_overflow `1/1` |
| `lanes.<lane>.window_remaining` | int | `sum(soft - window_total)`；**含维护/探针预留，不是生成可用次数** |
| `lanes.<lane>.daily_remaining` | int | `sum(daily - daily_total)`；只反映 generation 日计数 |
| `bound_accounts` | int | 当前绑定账号数（计数，不含账号标识） |
| `last_switch_at` | int | 最近一次账号迁移或 WARP 成功轮换的 Unix 秒；从未发生为 `0` |
| `last_replenish_at` | int | 最近一次补充命令尝试的 Unix 秒；从未发生为 `0` |
| `maintenance_queue` | int | 维护 `no_capacity` 积压计数（内存+持久化，重启恢复） |

**管理器白名单**：不返回 `socks_url`、token、出口 `id`、账号标识、`.ovpn`、`state.json`、`Node.Config`。

**app 白名单**：`_parse_status` 只读取以下 7 个标量，管理器多返回的 `cooling`/`lanes`/`last_replenish_at`/`maintenance_queue` 会被丢弃，不进入 `GET /config`：

```text
manager_ok, exits.healthy, exits.total, exits.quarantined, exits.disabled,
bound_accounts, last_switch_at
```

### 2.3 失败语义（app 侧降级形状）

HTTP 非 200（如 503）、传输超时、响应非 JSON、`exits` 不是对象、任意字段非数值，统一降级为全零：

```json
{
  "manager_ok": false,
  "healthy": 0,
  "total": 0,
  "quarantined": 0,
  "disabled": 0,
  "bound_accounts": 0,
  "last_switch_at": 0
}
```

- 单次探测上限 `STATUS_TIMEOUT_S = 2.0`。
- 结果缓存 `STATUS_CACHE_TTL_S = 15.0`：成功与失败都会缓存 15s（负结果缓存）。
- 冷缓存并发合流：同一时刻只有一个在途 `/v1/status` 探测，其余调用等待并复用同一结果。
- 该接口失败**只影响只读卡片**，不代表 `/v1/lease` 一定失败。

---

## 3. `POST /v1/lease`（主入口，按调用扣预算）

### 3.1 请求

```http
POST /v1/lease
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "account": "<HMAC-SHA256-hex>",
  "lane": "free",
  "class": "generation",
  "calls": 1,
  "request_id": "optional-idempotency-key"
}
```

| 字段 | 必填 | 取值/语义 |
|---|---|---|
| `account` | 是 | `HMAC-SHA256(AISTUDIO_EXIT_HMAC_KEY, account_id)` 的十六进制；管理器无法反推账号 |
| `lane` | 否 | `paid` / `free`；缺省 `free`；`free_overflow` 不能作为请求 lane |
| `class` | 是 | `generation` / `maintenance` / `probe`；兼容 `gen` / `maint` / `probe`；也接受 `category` 别名字段 |
| `calls` | 否 | 1..`budget.max_calls`（默认 1，上限 10） |
| `request_id` | 否 | 幂等键；同一 `(account,lane,class,calls,request_id)` 在 600s 内返回同一结果且不重复扣预算 |

> app 当前只发送 `account/lane/class/calls`，不发送 `request_id`；启用幂等前调用方应按返回的 `ttl_s` 重新租约，不要把 600s 当租约有效期。

### 3.2 成功响应（200）

```json
{
  "exit_id": "warp-1",
  "socks_url": "socks5://172.17.0.1:40110",
  "generation": 1,
  "ttl_s": 60,
  "budget_left": 49
}
```

- `ttl_s` 由 `budget.lease_min_ttl_s` / `lease_max_ttl_s` 夹在 **30~60s**；app 缓存同样夹到 `[30,60]`。
- `budget_left = soft_limit - window_total`，是“距软阈值的剩余总量”，**不是还能生成的次数**（生成还受维护/探针预留与 daily 限制）。
- `socks_url` 必须为 `socks5://` / `socks5h://`，主机在 `AISTUDIO_EXIT_ALLOWED_HOSTS`（默认 `127.0.0.1,172.17.0.1`）内且端口合法；否则 app 视为非法。
- `socks_url` 只返回给 app，绝不进 `/v1/status`/UI/日志；fanout 的 `socks_url` 含 `user:pass`。

### 3.3 无容量（429）

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
```

```json
{"error":"no_capacity","retry_after_s":60.0,"lane":"free","class":"generation"}
```

- **无预算/无同 lane 健康出口统一 429 + `retry_after_s`（60~600s）+ `Retry-After`**，不是 503、不是永久放弃。
- `retry_after_s` 取维护/探针/窗口重置/冷却/UTC 午夜等候选中的最小值并夹到 60~600s；daily 耗尽时可能指向窗口重置而非午夜，app 会在当天继续短退避重试。
- 维护/探针 `no_capacity` 会增加 `maintenance_queue` 并持久化；维护调用方按 `max(300s, retry_after_s)` 重试，保持账号 due，不写成功。

### 3.4 预算语义

- **车道隔离**：`paid` 只落 `lane=paid` 的 paid fanout；`free` 只落 `lane=free` 的 WARP；`free_overflow` 仅在“无 healthy free 出口 + free 维护积压 >0”时对 free 生效。paid 永不落 WARP、永不使用 overflow。
- **新绑定 vs 已绑定**：新绑定 generation 用 `soft` 上限；已有绑定且出口健康用 `hard` 上限；两者都先扣除 `maint_reserve + probe_reserve=26`。所以 paid 新绑定生成上限 4/窗口、已绑定 19/窗口；free 新绑定 24/窗口、已绑定 49/窗口。
- **日上限只约束 generation**：`daily_total` 只累加 generation；维护/探针不写日计数，日上限打满后仍可在窗口预留内前进。
- **预留**：maintenance 严格受 `maint_reserve=20`；probe 严格受 `probe_reserve=6`；生成不得占用。
- **粘性**：出口健康且 lane 一致 → 原样返回绑定；出口被隔离/烧毁 → 整组迁移；请求 lane 与绑定 lane 不一致 → 该账号重新绑定到目标 lane；无可用目标时返回 429 并保留旧绑定。
- **无按账号配额**：v2 没有 `account_soft_cap` / `per_account_window_cap` / `per_account_daily_cap`；单账号可吃满某出口的生成上限，需由出口数与监控兜底。
- **幂等**：`request_id` 结果持久化在 `idempotency.json`，保留 `IDEMPOTENCY_TTL_S=600s`；键含 `account+lane+class+calls+request_id`，跨重启有效、不同账号互不串用；同 id 不同参数不会命中同一键。

### 3.5 其他错误

| HTTP | body | 场景 |
|---|---|---|
| 400 | `{"error":"bad_request","detail":...}` | 参数非法、body 非 JSON/非对象、`calls` 越界 |
| 401 | `{"error":"unauthorized",...}` | Bearer 缺失/错误 |
| 403 | `{"error":"forbidden",...}` | 来源不在 `allowed_client_cidrs` |
| 404 | `{"error":"not_found"}` | 未知路径 |
| 405 | `{"error":"method_not_allowed",...}` + `Allow` | 方法不符 |

---

## 4. `GET /v1/assign`（兼容入口）

```http
GET /v1/assign?account=<HMAC-SHA256-hex>&kind=chat|build[&lane=free]
Authorization: Bearer <token>
```

- `kind ∈ {chat, build}`；绑定 `kind` 只在首次绑定/迁移时确定，之后同账号请求不会改写。`/v1/lease` 不读取 `kind`，因此经主入口建立的绑定恒为 `chat`；`/v1/assign` 可显式传入 `kind`。
- 内部等价于 `lease(class=gen, calls=1)`；没有 `request_id`，每次 GET 都计 1 次 generation。
- 成功 `200`：

```json
{
  "exit_id": "warp-1",
  "socks_url": "socks5://172.17.0.1:40110",
  "generation": 1,
  "ttl_s": 60,
  "healthy": true
}
```

- 无容量 → 同样 `429 {"error":"no_capacity",...}` + `Retry-After`；app **fail-closed** 不缓存失败结果，但生成主链已改用 `/v1/lease`，此入口仅兼容旧客户端/联调。
- `socks_url` 校验同上；`healthy` 必须显式为 `true`。

---

## 5. `POST /v1/events`（账号/出口级事件上报）

```http
POST /v1/events
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "account": "<HMAC-SHA256-hex>",
  "exit_id": "warp-1",
  "kind": "region_403",
  "canary": "exit_bad",
  "ts": 1764000000
}
```

| 字段 | 取值/语义 |
|---|---|
| `kind` | `region_403` / `verify_account` / `other_403` |
| `canary` | `exit_bad` / `account_bad` / `inconclusive` |
| `ts` | Unix 秒；缺省用管理器时钟 |

- `canary=exit_bad` → 出口进入 `quarantined` 并整组迁移；`account_bad` / `inconclusive` → 只记录。
- `kind=verify_account` 额外做**连坐烧毁**：同一 `healthy` 出口在 `verify_cluster_window_s=1800s` 内出现 ≥`verify_cluster_threshold=2` 个**不同账号**的 verify → `cooling` `burn_cooldown_s=7200s` 并整组迁移；单账号 verify 不烧出口。
- 连坐烧毁**只对 `healthy` 出口生效**；`quarantined`/`cooling`/`disabled` 只记录事件，不改变状态、不覆盖隔离原因、不绕过人工 `recover`。
- `disabled` 是配置硬开关：`canary=exit_bad` 的隔离、verify 连坐、健康探针、`_refresh`、`recover` 都不能把它复活；事件只落摘要，`/v1/events` 返回 `accepted/applied:false`，`recover` 返回 409。
- 成功 `202 {"status":"accepted","applied":true}`；重复事件 `202 {"status":"duplicate","applied":false}`（摘要持久化，重启后仍幂等）。
- app 侧事件上报是 fire-and-forget / 有界等待（`http_timeout_s`），失败只记录日志，不影响在途请求。
- app 仅在真实 `AuthError.verification_required` 且 `record_attempts=true`（真实生成路径）时上报 `verify_account`（`canary=inconclusive`）；countTokens 不上报，无绑定 `exit_id` 直接跳过；region 403 由 canary 仲裁上报 `region_403`；两者不双报。

---

## 6. `POST /v1/admin/recover`（人工恢复隔离出口）

```http
POST /v1/admin/recover
Authorization: Bearer <token>
Content-Type: application/json

{"exit_id":"warp-1"}
```

- 成功 `200 {"exit_id":"warp-1","status":"healthy","rotated":true,"detail":...}`。
- 流程：`quarantined → probing` →（warp 且 `rotate_on_recover=true`）调用 `rotate-instance.sh N` → 立即做一次 L1 探针 → 健康则放回池，失败则继续 `quarantined`。`health.enabled=false` 时跳过探测直接放回（仅建议维护窗口使用）。
- `404`：未知出口；`409`：`disabled` 出口；`400`：缺 `exit_id`。
- `recover` 中途进程被杀会留下 `probing`；重启自动归一（有错误/隔离时间 → `quarantined`，否则 `healthy`）。

---

## 7. app 只读暴露（`GET /config`）

`GET /config` 在既有字段之外新增（只读，无设置项、无 PUT）：

| 字段 | 取值 | 语义 |
|---|---|---|
| `proxy_mode` | `direct` \| `single` \| `external_pool` | `external_pool` 仅当 `AISTUDIO_EXIT_MODE=external` 且 endpoint/token/hmac 齐备；否则按 `settings.proxy_url` 返回 `single` 或 `direct` |
| `exit_pool` | 对象或 `null` | 仅 `external_pool` 时为上表 app 白名单 7 字段；其它模式恒为 `null` |

```json
{
  "proxy_configured": true,
  "proxy_mode": "external_pool",
  "exit_pool": {
    "manager_ok": true,
    "healthy": 3,
    "total": 4,
    "quarantined": 1,
    "disabled": 0,
    "bound_accounts": 12,
    "last_switch_at": 1764000000
  }
}
```

- `proxy_mode` 为 `single`/`direct` 时**不构建 resolver、不发任何管理器请求**。
- 旧字段 `proxy_configured` 保留，旧前端不受影响。
- 沙盒调试 query（`proxy_mode` / `manager_ok`）只在 `diagnostics.ui_debug` 沙盒内生效，生产路径完全忽略；卡片不展示 socks/token/出口 id/账号。

---

## 8. app 侧出口使用语义（fail-closed）

### 8.1 预算耗尽 vs 无出口

- **429 `no_capacity`**：`ExitBudgetWaitError`（`status_code=429`，`code=no_capacity`，`Retry-After=ceil(max(1,retry))`）；生成槽位保持 `UNSTARTED`，不换候选、不切号、不降级 free、不消耗账号 429 冷却。
- **503 `exit_unavailable`**：只用于管理器不可达/响应非法/账号无绑定等 fail-closed；实例层显式 re-raise，不改写为 409，也不把槽位置 `INVALID`；恢复后下一次请求可直接重建。
- **四协议渲染**：Gemini `RESOURCE_EXHAUSTED`；OpenAI/Responses `rate_limit_exceeded`；Claude `rate_limit_error`；均带 `Retry-After`。
- **已出正文**：继续，不仲裁、不切号、不切出口；本请求预算在开始时已计。
- **D-05**：绑定变化/出口烧毁只在下一次请求前重建（release → `active_requests==0` → close → restart → 一次重试），不热切换、不 blue-green。

### 8.2 应急阀 `AISTUDIO_EXIT_DIRECT_FALLBACK`

- **默认关闭**；仅 `1` / `true` / `yes` / `on`（大小写不敏感）视为开启。
- 设计口径：**仅 maintenance 可使用**；generation 永不直连。当前实现由 env 统一门控、尚未按 `class` 收窄；因默认关闭，生产口径以“仅 maintenance”作为目标，收窄列入下一步。
- 开启后，external 模式拿不到账号出口的带凭据请求**改为直连**（`proxy=None`），**绝不回退 `settings.proxy_url`**；WARNING 按账号 60s 限频。
- 未开启时同一情形 fail-closed：不构造传输、不发起任何上游请求；generation 映射 503，维护按 ≥300s 重试。

### 8.3 引擎范围

- external 模式**仅支持 `browserless` 引擎**；`mode=browser` + external 在 `build_engine_runtime` 阶段直接 fail-fast（`RuntimeError`），不会用进程级全局代理跑凭据流量。
- off 模式（未配置 `AISTUDIO_EXIT_MODE=external`）行为与改造前完全一致：仍使用 `settings.proxy_url`，`get_exit_resolver()` 返回 `None`，零管理器请求。

### 8.4 批量探活导入（`POST /accounts/probe-import`）

- external 下**不再整体 503**。流程固定为：
  1. 先用 Cookie jar 落库一个**真实账号 id 的锚点账号**（`auth_user=0`）；
  2. 用该真实 id 解析一次出口；
  3. 通过该出口调用 `probe_google_accounts_infinite(account_id=<真实 id>)` 探测 `u/0,u/1,...`；
  4. 按 `auth_user` 拆分：`u/0` 回写锚点账号，其余各建**真实 id** 新账号；
  5. 缺 email/tier 的账号用各自的真实 id 再做一次导入后探测。
- **禁止**把合成 `cookie_<hex>` 作为管理器账号键；合成值只可存为 `cookie_id`。
- 锚点 id 由**稳定身份 Cookie**（`SAPISID` → `APISID` → `SID` 取值哈希）派生，形如 `acc_import_<hash>`；**不得**使用每次变化的 `__Secure-1PSIDTS`/`SIDCC` 或整包 Cookie 哈希。
- 遇 `no_capacity` 只记录 deferred 并继续导入，不整批 503；探测失败/无出口时保留恰好一条可重试占位记录，原因写入 `last_refresh_error`（`no_exit` / `probe_error: ...`），响应附带 `skipped` / `skip_reason` / `probe_failed`。
- 同一请求多条目时，条目之间按 `IMPORT_PROBE_PACING_SECONDS=0.3` 串行间隔。

### 8.5 元数据刷新与保活

- `refresh_accounts` / keepalive 在 external 下：`no_capacity` → `deferred=True`、`retry_after_s` 透传；keepalive 按 `max(300s, retry_after_s)` 短周期重试，不推进 24h due，不改写 `tier_checked_at`，不计账号失败/冷却。
- 管理器不可达/载荷非法 → fail-closed；不发起上游请求，不 fallback 全局代理。
- API 响应同时给出 `deferred` / `skipped` / `skip_reason`，便于区分“预算排队”与“无出口/探测失败”。

### 8.6 signer daemon 的 `-proxy` 语义（代理发现修复已落地）

- Python 侧在 external 下**不向 daemon 传 `-proxy`**，并显式追加 `-no-proxy-discovery`；同时大小写不敏感地剥离子进程环境中的 `AISTUDIO_PROXY/HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`。
- waa-signer 侧 `proxydefault.Resolve` 的语义是“显式 `-proxy`（含空串 `""`）优先；否则 `-no-proxy-discovery` 下不探测；仅默认情况才调用 `discoverProxy()`”。因此 external 下省略 proxy 的 signer 请求真正直连，应急阀（`proxy=None`）在 signer 传输下同样不会回落到环境代理。
- 旧版 `waa-signer` 二进制不认识 `-no-proxy-discovery`；external 部署前必须用 cgo/SpiderMonkey 工具链**重建** signer 可执行文件。

### 8.7 Cookie 轮换（RotateCookies）

- 有池会话时优先走 `rotate_cookies_via_signer`（会话已绑定账号出口）。
- 无池会话时走裸路径：按 `account_id` 解析出口，凭据 RPC 使用该出口；`no_capacity` 返回 `deferred/skipped=no_exit`，不扇出兄弟账号、不写 Cookie；管理器不可达 fail-closed，绝不回退全局代理。

---

## 9. fanout 机器接口（`FANOUT_MACHINE_TOKEN`）与只读接口边界

fanout 是独立服务（非 exit-manager），在同一进程内同时提供管理面、只读面与机器面。管理器 v2 当前只使用只读面 `GET /api/readonly/tunnels` + `fanout.swap_command` 命令钩子；机器面是 fanout fork 提供的可选控制接口，**尚未被管理器接线**（`REV-2.1.5.14` N-5）。

### 9.1 鉴权与默认关闭

- **环境变量**：`FANOUT_MACHINE_TOKEN`。长度按 rune 计，**至少 16 个字符**；不足或不合法时进程启动即 `log.Fatalf`（启动前置校验，发生在恢复隧道/健康检查之前）。
- **未设置 token**：`/api/machine/*`（含 `/api/machine`、`/api/machine/`）返回**普通 404**，不是 401、不泄露“机器接口”文案；管理面 `/api/*` 仍按原登录逻辑返回 401。
- **Bearer**：所有机器接口请求需 `Authorization: Bearer <FANOUT_MACHINE_TOKEN>`；空 token 永不放行，比较为常量时间（SHA-256 后 `subtle.ConstantTimeCompare`）。
- **Loopback**：只接受 `127.0.0.1` / `127.x` / `::1` / IPv4-mapped loopback（`RemoteAddr`，不读 `X-Forwarded-For`）；非 loopback 返回 **404**，不写审计、不占限流桶。
- **限流**：按来源 IP 固定窗口 **30 次/分钟**；超限 429 + `Retry-After` + `{"error":"请求过于频繁","retry_after":N}`；不同 loopback 源各自独立桶（桶数 ≥64 时清理旧桶）。
- **请求体**：POST 上限 **4 KiB**，超限/非 JSON/字段非法返回 400；GET 只用 query。
- **方法**：端点方法白名单；方法不符 405 + `Allow`；未知端点 404。
- **反代边界**：fanout 默认可能监听所有网卡；同机 nginx 会让 `RemoteAddr` 恒为 loopback。**反向代理必须屏蔽 `/api/machine/*`**，或把机器接口改为独立 loopback 监听/Unix socket；否则机器接口仅靠 Bearer 保护。

### 9.2 端点

| 方法/路径 | 参数 | 语义 |
|---|---|---|
| `GET /api/machine/status` | 无 | 后端名、隧道计数、每出口 `slot/port/status/exit_ip/country/country_code/suspect/socks_url`；`socks_url` **含 socks 凭据**，仅供管理器内部使用 |
| `POST /api/machine/provision` | `region`（空或两位 ASCII）、`count` 1..50、`template` ≥0 | 触发出隧道，返回 `{"job":"..."}`；XCL 后端忽略 template；`Manager.maxSlots` 默认 20，超出可能异步部分失败（Q-9） |
| `POST /api/machine/swap` | `slot` ≥1 | 换节点；400 参数、404 槽位不存在、500 失败 |
| `POST /api/machine/stop` | `slot` ≥1 | 停隧道；400/404；并发 stop 竞态已对齐为 200/404，不再 500 |
| `POST /api/machine/start` | `host`（必填） | 按节点名启动；**`slot` 非空时（含与 host 同时出现）→ 400**；`?slot=` 空值目前未被拒（fanout Q-2） |
| `GET /api/machine/jobs` | 可选 `id` | JobView（id/状态/主机/出口 IP/错误/计数）；`Steps[].Detail` 为自由文本，未做严格白名单（Q-3） |
| `GET /api/machine/nodes` | 无 | 节点白名单：hostname/IP/country/country_code/ping/speed_mbps/sessions/residential；不含 `Node.Config` |

**数据边界**：机器接口的 `status` 允许返回含凭据的 `socks_url`（这是它与管理面/只读面的核心差异）；但不得返回 `.ovpn`、`Node.Config`、`state.json`、`password`、`socks_user`、`socks_pass` 原文；审计日志只记时间/来源/动作/目标/结果码/计数，不记 token、Authorization、请求头/体/响应体。

### 9.3 只读接口（`/api/readonly/*`，管理器实际使用）

```http
GET /api/readonly/tunnels
Authorization: Bearer <readonly_token>
```

- 只接受 `GET`；同样只允许 loopback；Bearer 为独立的 `fanout.readonly_token`。
- 只返回 `slot/port/status/exit_ip/country/country_code/suspect`；**不返回** `socks_url`、socks 凭据、`.ovpn`、`Node.Config`、`state.json`。
- 当前只读载荷**无隧道 `id`**：管理器优先按隧道 `id`（若有）匹配，否则按 `exits[].socks_url` 的端口与隧道 `port` 匹配；端口不一致时该出口的 `exit_ip` 记录与 `suspect` 检测会静默跳过（`DEPLOY.md` §7）。
- `swap_command` 仍是命令钩子 + TODO；机器接口的 `swap/provision` 尚未被管理器接线。若后续接线，必须使用 loopback + `FANOUT_MACHINE_TOKEN`，并保持 `auto_swap_fanout=false` 默认，只在隔离恢复时调用。

---

## 10. 兼容性与回滚

- 删除/清空 `AISTUDIO_EXIT_*` 后重启 app：resolver 不联网、`proxy_mode` 回落 `single`/`direct`、`exit_pool` 为 `null`，行为与改造前一致。
- 管理器侧字段新增必须向后兼容：app 只认 §2.2 白名单，未知字段被忽略。
- 旧契约已作废，联调不要按旧值断言：
  - ~~`GET /v1/assign` 为主入口~~ → `POST /v1/lease`；
  - ~~503 `no_healthy_exit` / `exit_unavailable` 表示无容量~~ → 无容量 429 `no_capacity` + `retry_after_s`；503 仅 fail-closed；
  - ~~`ttl_s=300`~~ → 30~60s；
  - ~~`granted/category` 成功字段~~ → `calls` 请求字段 + `budget_left` 响应字段；
  - ~~全出口统一 soft=50/hard=75/daily=2400~~ → paid 30/45/400、free 50/75/2400；
  - ~~日上限约束所有类别~~ → 日上限只约束 generation；
  - ~~`capacity` / 按账号容量~~ → 只按每出口调用预算，无按账号配额。
- `FANOUT_MACHINE_TOKEN` 置空：`/api/machine/*` 回到 404；只读接口与管理面不受影响（只读仍用 `readonly_token`）。
- 管理器回滚：停止 manager，恢复 `state/`/`config.json` 备份；app `AISTUDIO_EXIT_MODE` 置空即回到全局代理；不强制 WARP 轮换。
