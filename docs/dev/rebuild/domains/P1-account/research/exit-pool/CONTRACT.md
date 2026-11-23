# 出口管理器控制面契约（app ↔ exit-manager）

本文件固化 `aistudio-api`（app 侧）与宿主 `exit-manager` 之间的最小 HTTP 契约，
是 `data/tools/exit-manager/DEPLOY.md` §5 的仓库内镜像。实现事实来源：

- app 客户端：`src/aistudio_api/infrastructure/exit_pool/resolver.py`
- app 配置：`src/aistudio_api/infrastructure/exit_pool/config.py`
- 管理页只读字段：`src/aistudio_api/api/routes_system.py`（`GET /config`）

任何字段/语义变更必须同时更新本文件与 `DEPLOY.md`。

---

## 1. 通用约定

- **Base URL**：`AISTUDIO_EXIT_ENDPOINT`，仅接受 `http://` / `https://` 且主机非空；
  非法值使整个出口池保持关闭（`get_exit_resolver() is None`）。
- **鉴权**：所有 `/v1/*` 请求携带 `Authorization: Bearer <AISTUDIO_EXIT_TOKEN>`；
  token 必须与管理器 `config.json` 的 `token` 完全一致（前后空白会被裁剪，仍不一致即 401）。
- **来源限制**：管理器只接受 `security.allowed_client_cidrs` 内的源地址，否则 403。
- **监听面**：管理器仅允许 `127.0.0.1` / `::1` / `localhost` / `172.17.0.1`。
- **方法**：只读接口只接受 `GET`；事件只接受 `POST`。
- **超时**：app 侧控制面超时由 `AISTUDIO_EXIT_HTTP_TIMEOUT` 控制（默认 2s）。
- **失败非致命（fail-closed）**：任何超时 / 非 200 / 非 JSON / 非法载荷都只在 app 侧降级，
  **绝不抛出到请求路径管理层之外**；**分配失败一律 fail-closed：跳过本次带凭据请求，
  绝不回退全局 `AISTUDIO_PROXY`**（唯一例外是显式开启的应急阀
  `AISTUDIO_EXIT_DIRECT_FALLBACK`，见 §7.1）；状态失败仅让只读卡片显示“管理器状态不可用”。
- **远端部署**：`AISTUDIO_EXIT_ENDPOINT` 指向非 loopback / 非 docker 桥地址时必须使用
  `https://`，Bearer token 不得以明文 HTTP 跨主机传输。

---

## 2. `GET /v1/status`（只读观测）

### 2.1 请求

```http
GET /v1/status
Authorization: Bearer <token>
```

无查询参数、无请求体。

### 2.2 成功响应（200）

```json
{
  "manager_ok": true,
  "exits": {
    "total": 4,
    "healthy": 3,
    "quarantined": 1,
    "disabled": 0
  },
  "bound_accounts": 12,
  "last_switch_at": 1764000000
}
```

| 字段 | 类型 | 语义 |
|---|---|---|
| `manager_ok` | bool | 管理器已完成状态汇总；app 仅在严格 `true` 时视为在线 |
| `exits.total` | int | 配置中的出口总数 |
| `exits.healthy` | int | 当前可分配的健康出口数 |
| `exits.quarantined` | int | 被隔离、等待人工 `recover` 的出口数 |
| `exits.disabled` | int | 配置 `disabled: true`、永不分配的出口数 |
| `bound_accounts` | int | 当前绑定账号数（计数，不含账号标识） |
| `last_switch_at` | int | 最近一次账号迁移或 WARP 成功轮换的 Unix 秒；从未发生为 `0` |

**白名单**：app 只读取上表 7 个标量字段；管理器多返回的任何字段（如 `secret`、
`version`）都会被丢弃，不进入 `GET /config` 响应。

**不得返回**：`socks_url`、token、出口 `id`、账号标识或任何 HMAC 原文之外的敏感信息。

### 2.3 失败语义（app 侧降级形状）

HTTP 非 200（如 503）、传输超时、响应非 JSON、`exits` 不是对象、任意字段非数值，
统一降级为全零：

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
- 结果缓存 `STATUS_CACHE_TTL_S = 15.0`：成功与失败都会缓存 15s（负结果缓存），
  管理器恢复后卡片最多滞后 15s 才转为在线。
- 冷缓存并发合流：同一时刻只有一个在途 `/v1/status` 探测，其余调用等待并复用同一结果。
- 该接口失败**只影响只读卡片**，不代表 `/v1/assign` 一定失败。

---

## 3. `GET /v1/assign`（粘性分配）

```http
GET /v1/assign?account=<HMAC-SHA256-hex>&kind=chat|build
Authorization: Bearer <token>
```

- `account`：`HMAC-SHA256(AISTUDIO_EXIT_HMAC_KEY, account_id)` 的十六进制；
  manager 不持有 HMAC 盐，无法反推账号。
- `kind ∈ {chat, build}`；绑定 `kind` 只在首次绑定/迁移时确定，之后同账号请求不会改写。

成功 `200`：

```json
{
  "exit_id": "warp-1",
  "socks_url": "socks5://172.17.0.1:40110",
  "generation": 1,
  "ttl_s": 300,
  "healthy": true
}
```

失败与降级：

- 无可用出口 / 容量满 / 迁移失败 → `503 {"error":"no_healthy_exit",...}`；
  app **fail-closed**：不缓存失败结果，跳过该账号本次带凭据请求；
  **绝不回退全局 `AISTUDIO_PROXY`**（应急阀见 §7.1）。
- `socks_url` 必须为 `socks5://` / `socks5h://`，主机在
  `AISTUDIO_EXIT_ALLOWED_HOSTS`（默认 `127.0.0.1,172.17.0.1`）内且端口合法；
  否则 app 视为非法并回退。
- `healthy` 必须显式为 `true`；缺失/false 视为不可用，不缓存。
- app 侧缓存 TTL 取 `min(ttl_s, AISTUDIO_EXIT_TTL)`，默认上限 300s。

---

## 4. `POST /v1/events`（账号/出口级事件上报）

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

- `kind ∈ region_403 | verify_account | other_403`。
- `canary ∈ exit_bad | account_bad | inconclusive`。
- **只有 `exit_bad`** 触发隔离 + 账号迁移；其余仅记录。
- 成功 `202 {"status":"accepted","applied":true}`；
  重复事件 `202 {"status":"duplicate","applied":false}`（摘要持久化，重启后仍幂等）。
- app 侧事件上报是 fire-and-forget / 有界等待，失败只记录日志，不影响在途请求。

---

## 5. app 只读暴露（`GET /config`）

`GET /config` 在既有字段之外新增（只读，无设置项、无 PUT）：

| 字段 | 取值 | 语义 |
|---|---|---|
| `proxy_mode` | `direct` \| `single` \| `external_pool` | `external_pool` 仅当 `AISTUDIO_EXIT_MODE=external` 且 endpoint/token/hmac 齐备；否则按 `settings.proxy_url` 返回 `single` 或 `direct` |
| `exit_pool` | 对象或 `null` | 仅 `external_pool` 时为上表 7 字段状态；其它模式恒为 `null` |

- `proxy_mode` 为 `single`/`direct` 时**不构建 resolver、不发任何管理器请求**。
- 旧字段 `proxy_configured` 保留，旧前端不受影响。
- 沙盒调试 query（`proxy_mode` / `manager_ok`）只在 `diagnostics.ui_debug` 沙盒内生效，
  生产路径完全忽略；卡片不展示 socks/token/出口 id/账号。

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

---

## 6. app 侧出口使用语义（fail-closed）

### 6.1 应急阀 `AISTUDIO_EXIT_DIRECT_FALLBACK`

- **默认关闭**；仅 `1` / `true` / `yes` / `on`（大小写不敏感）视为开启。
- 开启后，external 模式拿不到账号出口的带凭据请求**改为直连**（`proxy=None`），
  **绝不回退 `settings.proxy_url`**；WARNING 按账号 60s 限频。
- 未开启时同一情形一律 `skip`，不构造传输、不发起任何上游请求。
- `skip` 日志按 `(账号, 原因)` 60s 限频，但当前是**两层各自独立限频**：
  `resolver._log_skip` 一条，`account_info_refresher` / `account_keepalive`
  共用 `skip_log_due` 一条。因此首次 skip 最多产生 **2 条** WARNING
  （resolver 与 refresher 各最多一条 / 60s），而不是“单次只产出一条”。
  本轮选择保留双层、文档如实（Q-2：不把跨层日志耦合进单一字典）。

### 6.2 引擎范围

- external 模式**仅支持 `browserless` 引擎**；`mode=browser` + external 在
  `build_engine_runtime` 阶段直接 fail-fast（`RuntimeError`），不会用进程级全局代理跑凭据流量。
- off 模式（未配置 `AISTUDIO_EXIT_MODE=external`）行为与改造前完全一致：
  仍使用 `settings.proxy_url`，`get_exit_resolver()` 返回 `None`，零管理器请求。

### 6.3 生成主链

- `engine_runtime.create_session` 按 `account_id` 解析出口；无出口/管理器不可达时抛
  `RoutingError(code="exit_unavailable", status=503)`，**不建立会话、不发送任何上游请求**。
- 该 503 必须端到端原样到达协议层（Gemini `UNAVAILABLE` / OpenAI `service_unavailable`）。
  实例层（`instance_pool.activate` / `instance_manager._activate`）对
  `exit_unavailable` **显式 re-raise**，不得包装成 409 `instance_not_ready`，也不得把槽位
  置 `INVALID`；槽位保持 `UNSTARTED`，管理器恢复后下一次请求可直接重建会话。
- `routing_pipeline.acquire` 对 `exit_unavailable` **直接终止候选循环**，不得当作
  “试下一个候选” 吞掉，也不得最终降级为 `surface_capacity`。

### 6.4 批量探活导入（`POST /accounts/probe-import`）

- external 下**不再整体 503**。流程固定为：
  1. 先用 Cookie jar 落库一个**真实账号 id 的锚点账号**（`auth_user=0`）；
  2. 用该真实 id 解析一次出口；
  3. 通过该出口调用 `probe_google_accounts_infinite(account_id=<真实 id>)` 探测
     `u/0,u/1,...`；
  4. 按 `auth_user` 拆分：`u/0` 回写锚点账号，其余各建**真实 id** 新账号；
  5. 缺 email/tier 的账号用各自的真实 id 再做一次导入后探测。
- **禁止**把合成 `cookie_<hex>` 作为管理器账号键；合成值只可存为 `cookie_id`。
- 锚点 id 由**稳定身份 Cookie**（`SAPISID` → `APISID` → `SID` 取值哈希）派生，
  形如 `acc_import_<hash>`；**不得**使用每次变化的 `__Secure-1PSIDTS`/`SIDCC`
  或整包 Cookie 哈希。这样重复导入同一 Cookie 落到同一条记录，天然幂等。
- 探测成功时：`u/0` 认领该锚点并回写 email/tier；若已存在同
  `email + auth_user` 的真实账号，则把本次导入合并进该账号并删除锚点；
  探活结果中**没有 `u/0`** 时删除锚点，避免留下孤儿半成品。
- 探测失败（无出口 / 探活异常）时：只保留**恰好一条**可重试的占位记录
  （同一 Cookie 反复导入仍是同一条，不会堆积），原因写入
  `last_refresh_error`（`no_exit` / `probe_error: ...`），响应附带
  `skipped` / `skip_reason` / `probe_failed` 字段。
- 同一请求多条目时，条目之间按 `IMPORT_PROBE_PACING_SECONDS=0.3` 串行间隔。

### 6.5 元数据刷新与保活

- `refresh_accounts` / keepalive 在 external 无出口时返回
  `ok=False, skipped=True, skip_reason=<no_exit|no_account>`；
  keepalive 按 300s 短周期重试，不推进 24h due，也不改写 `tier_checked_at`。
- API 响应同时给出 `skipped` 与 `skip_reason`，便于区分“探测失败”与“无出口跳过”。

### 6.6 signer daemon 的 `-proxy` 语义（代理发现修复已落地）

- Python 侧在 external 下**不向 daemon 传 `-proxy`**，并显式追加
  `-no-proxy-discovery`；同时大小写不敏感地剥离子进程环境中的
  `AISTUDIO_PROXY/HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`。
- waa-signer 侧代理发现修复已落地：`proxydefault.Resolve` 的语义是“显式
  `-proxy`（含空串 `""`）优先；否则 `-no-proxy-discovery` 下不探测；仅默认
  情况才调用 `discoverProxy()`”。因此 external 下省略 proxy 的 signer 请求
  真正直连，应急阀（`proxy=None`）在 signer 传输下同样不会回落到环境代理。
- 旧版 `waa-signer` 二进制不认识 `-no-proxy-discovery`（会报 flag 未定义）；
  external 部署前必须用 cgo/SpiderMonkey 工具链**重建** signer 可执行文件。

### 6.7 Cookie 轮换（RotateCookies）

- 有池会话时优先走 `rotate_cookies_via_signer`（会话已绑定账号出口）。
- 无池会话时走裸路径：按 `account_id` 解析出口，凭据 RPC 使用该出口；
  `no_exit` 直接返回 `skipped="no_exit"`，**不扇出兄弟账号、不写 Cookie**，
  绝不回退全局代理。

---

## 7. 兼容性与回滚

- 删除/清空 `AISTUDIO_EXIT_*` 后重启 app：resolver 不联网、`proxy_mode` 回落
  `single`/`direct`、`exit_pool` 为 `null`，行为与改造前一致。
- 管理器侧字段新增必须向后兼容：app 只认上述白名单，未知字段被忽略。
