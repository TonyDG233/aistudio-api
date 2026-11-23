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
- **失败非致命**：任何超时 / 非 200 / 非 JSON / 非法载荷都只在 app 侧降级，
  **绝不抛出到请求路径**；分配失败回退 `AISTUDIO_PROXY`（未配置即直连），
  状态失败仅让只读卡片显示“管理器状态不可用”。
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
  app 回退全局 `AISTUDIO_PROXY`，不缓存失败结果。
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

## 6. 兼容性与回滚

- 删除/清空 `AISTUDIO_EXIT_*` 后重启 app：resolver 不联网、`proxy_mode` 回落
  `single`/`direct`、`exit_pool` 为 `null`，行为与改造前一致。
- 管理器侧字段新增必须向后兼容：app 只认上述白名单，未知字段被忽略。
