# 出口池特殊模式评估（WARP + fanout，env 门控）

- 仓库：本仓工作区根目录
- 日期：2026-11-22
- 性质：**纯评估，不施工**。未修改 `src/`、`web/`、`android/`、`waa-signer/`、`tests/`、`defaults/`、`config.yaml`、`docker-compose*.yml`；未 `git add/commit`；未启动服务、未跑 pytest/构建；未访问 VPS、账号文件或任何带凭据的请求。
- 外网访问仅一次：经本地代理 `127.0.0.1:10809` 读取 `https://github.com/byJoey/fanout` 的 README 与少量结构/状态源码，用于判断部署条件与能力。
- 证据等级：`E1` 本仓代码/脚本直接可读；`E2` 外仓 README/源码；`U` 未验证或需实机确认。

## 0. 一句话结论

**最终系统应当是“browserless 引擎 + 进程外出口管理器 + 按账号的出口绑定 + app 侧鉴权级 canary 仲裁”的反应式出口池；默认 env 缺失时完全保持现状。** 其中“app 只认一个 socks 端口、管理器在端口后按账号分流”只在 browserless 下勉强可行；“fanout 作为无缝第二出口”因 fanout 自带的 10 秒节点漂移自动换 IP、公网 VPN Gate 质量与本地 DNS 行为，属于部分理想化；而“所有 `Region not supported` 都值得换出口”这个前提本身需要先排除本仓历史上“裸 httpx 指纹导致的 region 403”，否则出口池会治错病。

---

## 一、现状链路（file:line）

### 1.1 环境变量到全局 Settings

- `AISTUDIO_PROXY` 的发现顺序：`AISTUDIO_PROXY` → `HTTPS_PROXY` → `https_proxy` → `HTTP_PROXY` → `http_proxy`；未设置时还会探测本机 `7890/10808`：`src/aistudio_api/config.py:218-239`。
- 解析结果存入全局 `Settings.proxy_url`，在模块导入时求值一次：`src/aistudio_api/config.py:391-403`（字段在 403 行）。
- 浏览器代理字典构造（`server/username/password`）：`src/aistudio_api/config.py:242-259`。
- 当前部署值来自 compose：`docker-compose.yml:22` 与 `docker-compose.deploy.yml:18`，均为 `AISTUDIO_PROXY=${AISTUDIO_PROXY:-}`；VPS 实际为 `socks5://172.17.0.1:40001`（`data/tools/vps-exit-scripts/VPS-OPS-NOTES.md:17`）。

### 1.2 Python 侧所有消费点

| 消费点 | 读取方式 | 证据 |
|---|---|---|
| 本地 waa-signer 子进程 `-proxy` | 启动时传 `settings.proxy_url` | `src/aistudio_api/infrastructure/waa/engine_runtime.py:188-202`（201 行）；`local_daemon.py:93-111`（109-110 行拼接 `-proxy`） |
| browserless 每个槽位 session | `BrowserlessSession(proxy=settings.proxy_url)` | `engine_runtime.py:251-280`（271 行） |
| browserless mint proof | `proxy=self._proxy` | `browserless_session.py:549-560`（558 行） |
| browserless 403 后重新 mint | `proxy=self._proxy` | `browserless_session.py:615-624`（622 行） |
| browserless RPC | `proxy=self._proxy` | `browserless_session.py:716-726`（724 行） |
| browserless 流式 RPC | `proxy=self._proxy` | `browserless_session.py:752-763`（760 行） |
| browserless `direct_http` 传输 | `self._proxy or settings.proxy_url` | `browserless_session.py:879-903`（887 行） |
| 传统 browser 引擎 | 进程级 `--proxy-server` | `browser_engine.py:206-208` |
| tier/元数据裸 HTTP 探测 | `HttpxTransport` 固定 `settings.proxy_url` | `account_info_refresher.py:369-386`（374、385 行） |
| Cookie 解析/校验/轮换部分路径 | 直接读 `settings.proxy_url` | `cookie_parser.py:653、720、808、839`；`cookie_refresher.py:54` |
| 媒体 URL 下载 | `settings.proxy_url` | `format_converters/media.py:69-73` |
| UI 状态只读 | `bool(settings.proxy_url)` | `api/routes_system.py:782` |

关键点：`SignerClient` 到本地 signer 的控制连接使用 `trust_env=False`（`signer_client.py:224-252`），因此全局代理不会影响 Python↔signer 的本机通信；出口代理只作用于 signer 对上游的拨号。

### 1.3 Python → Go signer → 上游 的完整传递

1. Python `SignerClient.mint_proof(...)` 在 `proxy` 非空时写入请求体 `"proxy"`：`signer_client.py:344-374`（355、371-372 行）。
2. `SignerClient.send_rpc(...)` 通过 `_rpc_payload` 写入 `"proxy"`：`signer_client.py:419-446`、`556-583`（566、579-580 行）。
3. Go 请求契约已包含 `proxy` 字段：`waa-signer/internal/signerapi/types.go:34`（mint）与 `:61`（rpc）。
4. Go `WorkerPool` 本身有全局默认 `proxy`（来自 `-proxy` 或环境变量）：`waa-signer/cmd/waa-signer/main.go:128-138`、`:884-890`、`:1157`、`:1177`。
5. 每次 `AcquireWorker` 实际使用 `proxyOverride`（请求体里的 `proxy`），为空才回退全局：`main.go:501-522`（519-522 行）。
6. **Worker 复用键包含代理**：`entry.worker != nil && entry.proxy == effectiveProxy` 才允许热复用；代理变化会关闭旧 Worker 并重建（cold start）：`main.go:550-555`、`:572-587`、`:637-643`。
7. mint 与 rpc 两个 handler 都把 `req.Proxy` 传给 `AcquireWorker`：`main.go:1255`、`:1276`、`:1360`。
8. Go 的 SOCKS5 拨号器支持用户名/密码与 HTTP/HTTPS CONNECT：`waa-signer/internal/proxydial/proxydial.go:42-59`；`transport_browser.go:155-176`。

结论：**browserless 的“按账号不同出口”在当前结构里已经有完整注入点，且不需要改 Go signer 契约**。现成的 per-session 注入点是 `BrowserlessEngineRuntime.create_session(account_id=...)`（`engine_runtime.py:251-280`），现成的 per-worker/per-request 注入点是 `AcquireWorker(proxyOverride)` 与 `mint/rpc` 的 `proxy` 字段。当前唯一缺的是“在 `create_session` 里按 `account_id` 解析出代理”，而不是传输层能力。

### 1.4 “按账号选不同出口”的最少改动路径

- **browserless（可行，S）**：`create_session` 已经是 async 且已收到 `account_id`（`engine_runtime.py:251-260`、`instance_pool.py:682-689` 传入 `slot.account_id`）。在 `BrowserlessEngineRuntime` 内注入一个异步 `ExitResolver`，把 `engine_runtime.py:271` 的固定 `settings.proxy_url` 换成该账号解析结果即可；`BrowserlessSession` 的 mint/rpc/direct_http 会自动使用它（1.2 表）。`InstancePool` 与 `InstanceManager` 接口不需要改。
- **切换出口（不是首次绑定）**：`BrowserlessSession._proxy` 只在构造时赋值（`browserless_session.py:218-246`），会话存活期间不会重新解析。要让“确认出口 403 后换出口”生效，必须在以下三者中选一个：
  1. 给 session 增加受控的代理更新入口，下一次 mint/rpc 使用新出口；Go Worker 因 `entry.proxy` 不一致会原地重建（`main.go:550-587`），不需要新建 Page/Context。规模 S，但这是“请求中途改变出口身份”，需要单独拍板。
  2. 当前请求直接失败，标记该账号→该出口绑定失效，下一次 `create_session` 用新出口。规模 S，最保守。
  3. 复用 `_try_rebind` 的 D-05 形态，对同一账号做“换出口重建槽位”：释放句柄 → 检查 `active_requests==0` → 关闭旧会话 → 用新出口重建。规模 M，最符合 §4.3/4.4，但 `InstanceManager` 目前只支持换账号（`routing_pipeline.py:1161-1305`、`instance_pool.py:671-705`）。
- **browser（不可行/需新模块）**：Chrome 的代理是进程级参数（`browser_engine.py:206-208`），`Target.createBrowserContext` 当前只传 `disposeOnDetach`，没有 `proxyServer`/`proxyBypassList`（`cdp_client.py:937-950`）。要做到 per-account 出口，需要新增 per-context 代理参数或每账号独立浏览器进程；这不是现有结构直接支持。默认引擎是 `browser`（`AGENT.md:198-219` 的引擎定义与默认），因此**特殊出口池应明确限定 `engine.mode=browserless`**。
- **账号级探测路径仍是全局出口**：tier/元数据探测在 browserless 下优先复用池会话（`account_info_refresher.py:176-180、859-873`），但拿不到池会话时会回退到 `HttpxTransport` 的全局 `settings.proxy_url`（`account_info_refresher.py:369-386`）；Cookie 解析/校验也有直接读全局代理的路径（`cookie_parser.py:653、720、808、839`；`cookie_refresher.py:54`）。这些不属于生成主链，但会影响“该账号在哪个 IP 上被探测”的一致性，特殊模式若要求“账号所有上游动作都走绑定出口”，就必须把这些路径也接入解析器；否则只能接受“探测走全局出口”的例外。

### 1.5 现有出口脚本画像（为什么不能直接当池）

- 单实例：`warp-health-check-v2.sh` 的 `PROXY="socks5h://127.0.0.1:40000"`（28 行），docker 侧端口 `40001`（29 行）；`wireproxy-rotate.sh:46-53` 只写一份 `/etc/wireproxy/wireproxy.conf` 并 `systemctl restart wireproxy`，一次轮换影响全部流量。
- 轮换条件仍是“12MB 流量 / 35 分钟 + 500KB 活跃”：`warp-health-check-v2.sh:42-45`、`:203-229`；v1 相同阈值：`warp-health-check.sh:16-19`、`:129-156`。
- 健康检查分层：Gemini 面匿名探测 `check_google_health`（`warp-health-check-v2.sh:88-103`）；MakerSuite 匿名面 `check_makersuite_region`（`:105-121`）；但 VPS 实测结论是“MakerSuite 面鉴权在前、区域评估在后，匿名探针只能证明鉴权层可达，不能证明 IP 对登录用户干净”（`VPS-OPS-NOTES.md:61-84`）。
- **重要反证**：VPS 复盘认为当时的 `[7,"Region not supported."]` 最大嫌疑是“账号刷新的裸 httpx 请求配方”，同一出口上 signer 模型发现成功（`VPS-OPS-NOTES.md:28-35`）。这说明“看到 region 403 就换出口”在本仓历史上可能是误判；必须先确认 403 来自与真实生成一致的 signer/Firefox 路径，而不是 tier/元数据裸探测。

---

## 二、外部出口管理器契约比较与推荐

### 2.1 三种契约

#### A. app 只认单个 socks 端点，管理器在同一端口后按账号分流

- 可行性：**仅 browserless 可行，且必须把账号身份塞进 SOCKS5 认证信息**。Go `proxydial` 已支持 SOCKS5 用户名/密码（`proxydial.go:49-59`），httpx 也支持 `socks5://user:pass@host:port`；管理器需要实现“按 SOCKS5 用户名选择后端 wireproxy/fanout 端口”的前端。
- 账号标识怎么传：在解析出的 per-account 代理 URL 里使用 `HMAC(account_id)` 作为 SOCKS5 用户名（或 `user:pass`），管理器只看到不可逆标识，不接触 Cookie。Python 侧只改 `create_session` 的代理解析，Go signer 不用改（`mint/rpc` 的 `proxy` 字段原样传递）。
- 限制：Chrome 无法用 `--proxy-server` 携带每账号认证（`browser_engine.py:206-208`），传统 browser 引擎不可用；SOCKS5 认证在每次连接上都携带账号标识，管理器日志/连接表会看到它（用 HMAC 可接受）；管理器成为所有出口的单点，故障时只能回退全局 socks。
- 规模：管理器新增认证 SOCKS 前端 + 后端端口映射，约 300-600 行；app 侧 S。**不是最推荐**，因为把“账号→出口”的选择权藏在代理协议里，排障与审计都更难。

#### B. app 通过本地控制端点申请“账号→出口”映射（推荐）

- 形态：管理器在 `127.0.0.1` 或 docker 桥接地址暴露只读/事件 HTTP（或 unix socket）；app 在 `create_session` 时用 `HMAC(account_id)` 请求 `GET /v1/assign`，拿到 `{exit_id, socks_url, generation, ttl_s}`，把 `socks_url` 注入该 session（1.4 的注入点）。真实 403 后 app 先做 canary 仲裁，再 `POST /v1/events` 上报 `exit_bad/account_bad/inconclusive`；管理器据此隔离出口、迁移绑定、补位。
- 为什么 canary 必须在 app 侧：管理器若没有账号 Cookie，就无法做“鉴权后 region”判定；VPS 已证明匿名探针探不到鉴权后风控（`VPS-OPS-NOTES.md:61-84`）。管理器只做出口生命周期、粘性表、健康与分配，不读账号文件。
- 默认关闭：`AISTUDIO_EXIT_MODE` 缺失/非 `external` 时，resolver 直接返回 `settings.proxy_url`，不发起任何控制请求；管理器的存在与否对 app 无影响。
- 失败降级：控制端点超时/5xx/返回非法 URL → 该账号本次 `create_session` 用全局 `AISTUDIO_PROXY`，并记录一次降级事件；绝不因为管理器故障而拒绝请求或改变账号调度。
- 安全边界：控制端点只监听 loopback 或 docker 桥接地址 + Bearer token；app 只发送 HMAC 标识、事件分类、出口 id，不发送 Cookie/邮箱/auth.json；管理器脚本只放 `data/`（`.gitignore:12` 忽略），不读 `data/accounts/`。
- 规模：app 侧新增 exit resolver + canary 仲裁 + 事件上报，约 300-500 行；管理器（WARP/fanout 生命周期、绑定表、健康、隔离）约 600-1000 行 + 运维脚本。无 Go signer 改动。

#### C. app 直接读管理器维护的分配文件（file-watch）

- 形态：管理器原子写 `assignments.json`（临时文件 + `os.replace`，与 `account_store._atomic_write_json` 同风格），app 按 mtime + TTL 读取；默认 env 关闭时不读。
- 优点：没有控制端点暴露面，容器内可通过 bind mount 读取；实现最小（S，约 150-250 行）。
- 缺点：单向、无确认；文件过期或管理器崩溃时 app 只能回退；无法及时把“出口被隔离”推给 app；事件上报仍需另一条通道（否则 403 事件只能写日志，管理器无法反应）。因此 C 只适合作为 B 的降级/只读缓存，不适合作为完整方案。

### 2.2 推荐

**主方案 B；C 作为管理器不可达时的只读缓存或最小 v0。A 仅在“只跑 browserless、且不想让 app 增加控制端点”时作为备选，不推荐作为长期形态。**

推荐 B 的具体契约边界（评估口径，不是施工指令）：

```text
GET  /v1/assign?account=<hmac>&kind=chat|build
     -> 200 {exit_id, socks_url, generation, ttl_s, healthy}
     -> 404/503 表示无可用出口 -> app 回退全局 AISTUDIO_PROXY

POST /v1/events
     {account:<hmac>, exit_id, kind:"region_403"|"verify_account"|"other_403",
      canary:"exit_bad"|"account_bad"|"inconclusive", ts}
     -> 202 仅确认收到；不阻塞请求
```

默认行为：env 缺失 → `proxy_mode=off`，resolver 不构造、不联网、不改变 `settings.proxy_url`，UI 不新增块。失败降级：控制端点不可用 → 全局 socks；返回的 `socks_url` 必须通过 scheme/host/port 白名单校验，拒绝非 `socks5/socks5h/http/https` 或非预期主机的值，防止管理器被攻破后把 app 变成任意代理客户端。

---

## 三、403 分类、canary 与切换钩子

### 3.1 403 在现有链路的准确产生与处理位置

- 统一分类函数：`domain/errors.py:277-294`。当前顺序是：verify-account 标记 → `AuthError("禁止访问(需验证账号)")`（283-286）；grpc 8/HTTP 429 → `UsageLimitExceeded`（287-288）；grpc 16/HTTP 401 → `AuthError`（289-290）；grpc 7/HTTP 403 → **普通 `AuthError`**（291-292）。`[7,"Region not supported."]` 目前没有任何出口级语义，只是普通 403。
- 非流式生成：`gateway/client.py:303-304`；实例客户端：`gateway/instance_client.py:170-171`、`208-209`；流式：`gateway/streaming.py:233-241`。
- 路由处理：`routing_pipeline.py:915-1001`。`SessionExpiredError` 先切号（930-937）；`AuthError` 中 `verification_required` 单独隔离并切号（939-958）；其余 403 先同账号重新签名重试一次（969-989），失败后 `_switch_or_raise(reason="auth_error")`（999-1001）。`_switch_or_raise` 会把当前账号加入 `failed_accounts` 并走 `_try_rebind` 换账号（1742-1801）。
- 统计侧：`account_rotator.record_auth_error` 只加 `auth_errors`、`auth_cooldown=0`（374-391）；`record_session_expired` 才是 24h 锁（393-407）。`account_orchestrator` 把 `verification_required` 折叠成 `record_session_expired`（72-82），这是 verify-account 研究已指出的待拆分点。
- 模型发现的 403 分类是**另一套**：`model_discovery.py:44-74` 已区分 `region_restricted/verification_required/service_restricted/unknown_403`，并在 signer 通道首个 403 即停（810-840）。这套分类没有复用到生成链路的 `classify_error`。

结论：**目前“region 403”会被当成普通鉴权重试并最终换账号**，而不是换出口；出口池的 403 钩子必须插在 `AuthError` 分支的通用 403 之前，且不能走 `_switch_or_raise` 的账号失败记账。

### 3.2 分类建议

- 在 `domain/errors.py` 增加与 `verification_required` 同形的 `region_restricted` 标志（或子类），识别 `region not supported` / `available-regions`；保持 `classify_error` 纯领域、无 FastAPI。
- 分类优先级保持：verify-account（账号级）→ region（出口级嫌疑）→ 其他 403（仅记录）。
- 只在“真实生成/计数路径”上触发出口级判定；**不要把 tier 探测、Cookie 校验、媒体下载的 403 混进来**。理由是 VPS 复盘已证明这类裸 httpx 路径可能在干净出口上误报 region（`VPS-OPS-NOTES.md:28-35`）。
- 已出正文后的 403 不切换：现有 `has_yielded_data` 分支已经拒绝切号（`routing_pipeline.py:933-937、954-955、966-968`）；出口切换同样应只标记、不中断已开始的流。

### 3.3 canary 仲裁设计

- canary 账号落地：用专用低价值 Free 账号，**保持 `enabled=false`**，这样它不会被实例调度选中：`account_rotator.py:653、718、876`；`instance_manager.py:1223`。只要 `auth.json` 存在，`get_auth_path_optional` 仍可用（`account_rotator.py:732-734` 已按此过滤）。
- 探针执行：app 侧用现有 `SignerClient` + `BrowserlessSession`（或一个轻量 probe session）以 canary 的 Cookie、canary 自己的 `account_key`、**与真实请求相同的出口**发送一次小型鉴权 RPC（ListModels / GetAiStudioBenefitTier），而不是生成请求。它不占实例槽位，但会占用 signer 的一个 Worker；Worker 池满时会淘汰最久未使用的空闲 Worker（`main.go:617-625`），因此必须限定频率与并发。
- 判定：
  - canary 返回 region 403 → **exit_bad**，管理器隔离该出口并给该账号换出口；
  - canary 成功 → **account_bad**（出口对另一账号可用），不换出口；当前账号标记为 region/verify 一类账号级状态（具体字段需用户拍板），并切号；
  - canary 返回 verify-account 或其他非 region 失败 → **inconclusive**，不隔离出口、不换出口，只记录；同时把该 canary 账号视为受污染并停用。
- 频率：每个出口一个冷却窗口（例如 10-15 分钟）+ 全局并发 1；canary 失败不立即重试，避免把 canary 打进 verify。探针超时短于生成请求，且不进入请求统计的成功/失败分母。
- 是否违反红线：canary 不创建实例、不占 `active_requests`、不改账号调度；它只使用 signer Worker。只要“探针失败不阻塞准入、不作为第二套并发账本”，就不违反 §4.3。真正需要拍板的是：是否允许在请求路径里同步等待 canary（会增加时延）——建议异步/有界等待，超时按 inconclusive 处理。

### 3.4 切换钩子的干净落点

- 第一落点：`routing_pipeline.handle_error` 的 `AuthError` 分支，在 `getattr(exc, "verification_required")` 之后、通用 403 之前增加 `region_restricted` 分支。
- 该分支**不得**调用 `_switch_or_raise`（它会 `failed_accounts.add` 并换账号，1742-1801），也不得给账号加 429/auth 冷却（`account_rotator.py:374-391` 已明确 403 不加锁）。
- 切换执行的三条边界（对应 1.4）：
  1. 仅更新 session 代理 + `clear_templates` + 同账号重试（S，但改变请求中途出口身份）；
  2. 当前请求失败、仅标记绑定失效，下一次 `create_session` 用新出口（S，最保守）；
  3. 同账号“换出口重建槽位”，复用 `_try_rebind` 的“释放句柄 → `active_requests==0` → 关闭旧会话 → 重建”形态（M，最贴合 D-05）。
- 推荐裁决：**架构上选 3；如果本轮只想最小风险，选 2；1 需要单独拍板**。理由：D-05 只允许“请求触发的单槽重建”，不允许在旧 Page/Context 未 drain 时先建新页（`08-09-decision-record.md:17`；`AGENT.md:236-241`）。browserless 没有 Page/Context，但“换出口重建 Worker”仍应遵守同一 `active_requests` 纪律。

---

## 四、WARP + fanout 联合池模型

### 4.1 出口注册表（统一抽象）

每条出口至少需要：

```text
exit_id, kind(warp|fanout), socks_url, host, port,
region/country, identity_generation, status(healthy|quarantined|probing|disabled),
last_verified_at, last_exit_ip, latency_ms,
max_accounts, active_accounts, last_error, bound_accounts[]
```

WARP 与 fanout 的差异只体现在“如何产生 socks_url / 如何换身份”，对 app 暴露同一个 socks 端点。app 不关心底层是 wireproxy 还是 netns。

### 4.2 WARP 多身份

- 现状是**单身份**：一份 `/etc/wireproxy/wireproxy.conf`、两个 bind（`127.0.0.1:40000` 与 `172.17.0.1:40001`）、一次 `systemctl restart wireproxy`（`wireproxy-rotate.sh:46-53`）。这不能支撑“多出口”，因为一次轮换会切断所有流量。
- 多身份需要 N 份配置/N 个进程（例如 N 个 systemd unit 或一个 supervisor），每个身份独立 WireGuard key、独立端口、独立出口 IP；换身份只重启该实例，不动其他出口。规模 M（脚本 + 进程管理），不涉及 app 代码。
- WARP 轮换必须遵守“确认出口级 403 才换”，不能沿用 12MB/35 分钟定时阈值（`warp-health-check-v2.sh:42-45、203-229`）。且换身份前要确认该出口没有在途请求；现有脚本的 `wait_for_quiet_gap` 只是流量静默启发式（71-86 行），不是 `active_requests`，不能替代 app 的并发账本。
- WARP 的 DNS/IPv6：`wireproxy-rotate.sh:34-44` 配置了 `DNS=2606:4700:4700::1111,1.1.1.1` 与 v4/v6 地址；signer 的 SOCKS5 拨号保留域名（`proxydial.go:42-59`），DNS 由代理侧解析，通常比本地解析更适合 geo 判定。健康检查应同时覆盖 A/AAAA 连通性，但不要用“能连”替代“鉴权后 region 干净”。

### 4.3 fanout

- 能力（E2）：fanout 用 netns + 官方 openvpn，把 VPN Gate 公共节点包装成“一端口一出口”的 SOCKS5；每个出口独立 netns，母机网络不受影响；换节点时端口不变、出口 IP 变（README《原理》《使用》《运维》）。
- 状态：`/var/lib/fanout/state.json` 只持久化 `slot/port/hostname/country/config/socks 凭据`，**不含 exit_ip/status**（`state.go` 的 `persistedTunnel`）；运行态在 manager 内存，重启重建。管理界面是随机路径 + 口令的 HTTP（README《安装》），没有文档化的机器 API；`exits.go` 的 `Exit` 结构含 `exit_ip/status/socks_user/socks_pass`，但它们来自运行时视图，不是稳定控制契约。
- 关键冲突 1：**fanout 自带健康自动换节点**——每 10 秒比对出口 IP，连续两次不符就自动换节点（README《运维》）。这与“账号粘性绑定出口、不主动轮换”直接冲突：账号绑的是端口，端口不变但 IP 会变，等价于在 app 不知情时换了出口。除非禁用/改造该自动换节点，否则 fanout 不能作为“稳定出口”。
- 关键冲突 2：fanout 需要 root、Linux netns、`/dev/net/tun`，很多 LXC 小鸡没有 TUN（README《安装》）。它应跑在 VPS 宿主，不应塞进 app 容器；容器内默认没有 netns/TUN 权限。
- 关键冲突 3：VPN Gate 是志愿者公共节点，有相当比例下线/满员（`AUTH_FAILED`），且默认“只用家宽”也只是降低机房特征，不等于干净。对 Google 的 verify/region 风险高于 WARP。
- 关键冲突 4：README《已知限制》明确“域名仍在本机解析”。如果 app 的 SOCKS5 拨号以域名形式发出，fanout 侧本地解析可能造成 DNS 泄漏/geo 误判；需要在管理器侧验证实际解析位置，不能假定与 WARP 一致。
- 结论：**fanout 可以作为“WARP 出口被确认 region 封锁后的第二来源”，但必须满足三个前提：能关闭自动换节点、能通过只读 API/状态文件拿到 exit_ip/status、能接受公网 VPN 的质量与风控成本。** 否则“WARP + fanout 无缝联合池”是理想化描述。

### 4.4 健康检查分层

| 层 | 探测 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| L0 | TCP/SOCKS 握手 | 端口活着 | 上游可达 |
| L1 | 匿名 Gemini 面（`warp-health-check-v2.sh:88-103`） | 出口在 Gemini API 面未被 region 封锁 | AI Studio RPC 面、账号级风控 |
| L2 | 匿名 MakerSuite 面（`:105-121`） | 鉴权层可达（弱信号） | 鉴权后 region/verify（VPS `:61-84`） |
| L3 | canary 鉴权 RPC（app 侧） | 该出口对真实登录账号是否 region 干净 | 对全部账号都干净（只能抽样） |

只有 L3 的 region 失败才能触发出口隔离。L1 的 region 失败可以作为“不要分配新账号”的快速信号，但不能单独作为换出口依据；L2 只能记录。

### 4.5 故障与切换流程

```text
真实生成 403
  -> classify: verify_account? 是 -> 账号级，标记/切号，不换出口
  -> classify: region? 否 -> 仅记录/按普通 403 处理
  -> region 是 -> canary(同一出口)
       -> exit_bad -> 管理器 quarantined(exit)
                      -> 该出口所有绑定账号迁移到健康出口
                      -> 当前账号按 1.4 的边界重建/失败
                      -> 候选出口先 canary 验证，通过才启用
       -> account_bad -> 不隔离出口；账号标记 + 切号
       -> inconclusive -> 不换出口；记录；当前请求按普通 403 失败或一次重签
```

隔离不是“立刻重启 WARP/fanout”：有在途请求的出口只停止新分配，等在途结束再处理；没有在途才执行换身份/换节点。恢复要经过 canary，不能仅凭 L1 通过就放回池。

### 4.6 账号粘性持久化在哪

**管理器侧。** 理由：

- 出口绑定是“外部资源分配”，应在 app 重启、引擎切换、容器重建后保持；放 app 侧会与 `account_store` 耦合，且每次引擎切换都可能丢失。
- 管理器已经掌握出口生命周期与健康；绑定表与隔离状态在同一处，避免 app 与管理器双写冲突。
- app 只需要一个 TTL 缓存 + 控制端点失败回退；不在 app 里持久化出口映射，避免第二套事实源。
- 管理器只保存 `HMAC(account_id) -> exit_id + generation + bound_at + last_confirmed_at`，不保存 Cookie/邮箱/auth.json。

### 4.7 每出口并发/速率

- 不用 12MB 流量作为指标（那是 v1/v2 的轮换阈值，不是分配依据）。用“每出口最大账号数 + 最大并发请求数 + 最近 RTT/失败率 + 冷却”做分配约束。
- 管理器可以通过 app 的周期性心跳拿到每出口 `active_requests` 汇总（app 侧已有唯一账本，`AGENT.md:229-233`），但**管理器不得据此做请求准入**；它只用于“不要继续往这个出口塞账号/是否可安全换身份”。app 的 `active_requests` 仍是唯一准入原语，不能新增第二套并发账本。
- 出口级隔离只影响分配与换身份时机，不改变实例池容量、Free/Pro lane、tier 选择器（`AGENT.md:221-228`）。

---

## 五、UI 只读状态方案

- 现有网络代理卡片在 `web/src/views/SettingsView.vue:432-452`，数据来自 `GET /config` 的 `proxy_configured`（`api/routes_system.py:754-792`，782 行）；前端类型 `web/src/types/system.ts:5`；测试 fixture `web/tests/systemApi.test.ts:34`。
- 最合适的位置：**沿用 SettingsView 的“网络代理状态”卡片**，在特殊模式开启时把文案从“已配置代理”改为“外部出口池（只读）”，展示健康出口数/总数、当前账号绑定出口的脱敏标签、最近一次切换时间、管理器可达性；默认模式仍显示现有“已配置代理/直连”，不新增设置项。
- API 最小改动面：在 `/config` 响应增加只读字段，例如 `proxy_mode: "off"|"single"|"external_pool"` 与 `exit_pool: {healthy,total,bound_label,last_switch_at,manager_ok}`；env 缺失时返回 `proxy_mode="single"`、`exit_pool` 为 `null`。不改 `defaults/config.schema.json`，不新增 `config.yaml` 键，不新增 PUT。
- 备选：`GET /api/system/status`（`routes_system.py:239-290`）已经承载运行态，可在 Dashboard 加一行只读状态；但 SettingsView 的卡片已经存在，改动面更小。
- 风险：
  - `web/src/types/system.ts` 与 `web/tests/systemApi.test.ts:34` 需要同步默认字段，否则类型/测试会红；这是 S 级但必须列入改动面。
  - UI Debug 沙盒（`ui_sandbox`）也必须返回 `proxy_mode` 的稳定值，避免沙盒页面读不到字段；不得在沙盒里读真实 env 或访问管理器。
  - 不得展示 socks URL、Bearer token、账号 id、出口 IP 明细；只展示计数/脱敏标签/时间。UI 是只读，不提供“切换出口/重连/换节点”按钮。
  - 这符合 D-12“新设置按现有 UI 增量承载，不重构布局/导航”的口径（`AGENT.md:345`）。

---

## 六、分发与安全边界

### 6.1 env 门控是否真的“默认构建无感”

- 可行，但要求所有新增路径在**读取 env 之前不联网、不构造 resolver、不改 `settings.proxy_url`、不改 UI 响应形状**。`Settings` 是导入时求值的 dataclass（`config.py:391-403`），新增 `exit_mode` 字段本身无害；真正的风险是模块导入时顺手创建 HTTP client 或读文件。建议把出口池做成惰性模块，`AISTUDIO_EXIT_MODE != external` 时直接返回全局代理。
- 未知值处理：建议 `external` 之外一律视为 `off` 并打一条 warning；不要让拼写错误静默启用错误模式，也不要让它改变默认行为。

### 6.2 docker-compose 是否会泄漏特殊 env

- 当前 compose 的 `environment:` 只显式列出 `AISTUDIO_PROXY` 等既有变量（`docker-compose.yml:19-29`；`docker-compose.deploy.yml:15-25`），没有 `AISTUDIO_EXIT_*`。但 compose 同时启用了 `env_file: ./.env`，并注明“inject every var from ./.env when present”（`docker-compose.yml:11-13`；deploy `:8-10`）。因此**如果用户把 `AISTUDIO_EXIT_*` 写进 `.env`，它会被全量注入容器**；这不是 compose 白名单能挡住的。
- 所以“默认分发无感”的真实保障是：默认分发的 `.env`（若提供）不包含特殊变量、`environment:` 不新增特殊变量、代码在 env 缺失时不联网。特殊模式属于用户主动在 `.env` 或 `docker run -e` 中开启的自托管高级用法；文档必须明确写出这一点，不能宣称“compose 白名单天然隔离”。
- 容器内 `docker inspect`/`/proc/1/environ` 能看到 env；`LocalSignerDaemon` 启动 signer 时 `env=os.environ.copy()`（`local_daemon.py:121-126`），所以 token/HMAC 盐也会被 signer 子进程继承。若管理器 token 是高敏感值，建议不通过全局 env 传给 signer（或启动前 scrub），否则至少要在文档里承认这个可见面。代理 URL 本身不应带密码；若必须带 SOCKS 认证，使用 HMAC 用户名 + 独立密码，并避免日志打印完整 URL。

### 6.3 管理器脚本不进分发

- `data/` 已被 `.gitignore:12` 忽略；`git check-ignore` 确认 `data/tools/vps-exit-scripts/*` 不会入库（VPS-OPS-NOTES 也声明“永远不会被提交或上传”，`:3-5`）。
- Docker 构建上下文排除 `data/`（`.dockerignore:41`）、`docs/`（`:69`）、`scripts/`（`:74`）；Dockerfile 只 `COPY src/ main.py defaults/config.yaml docker-entrypoint.sh`（`Dockerfile:200-205`），不会把管理器脚本烤进镜像。
- 结论：**脚本放 `data/` + 默认 `.env` 不含特殊变量 + Dockerfile 不复制 data，共同保证默认分发包没有管理器。** 文档层面需要写明：特殊模式需要用户自备宿主级管理器、WARP 多实例与可选 fanout；本项目不附带、不自动安装、不读取账号文件；`env_file` 会全量注入用户自己的 `.env`，因此“无感”依赖默认 `.env` 干净，而不是 compose 白名单。

### 6.4 安全边界清单

- 管理器只监听 loopback 或 docker 桥接地址，Bearer token 必须非空；禁止 0.0.0.0 公网监听。
- app 只发 `HMAC(account_id)`、出口 id、事件分类，不发 Cookie/邮箱/Authorization/auth.json。
- 管理器不读 `data/accounts/`；canary 的 Cookie 只在 app 进程内使用。
- app 对 `socks_url` 做 scheme/host/port 白名单校验，防止管理器被攻破后变成任意代理。
- 事件上报不阻塞请求；管理器故障回退全局 socks，不改变账号可用性。
- 日志/UI 不输出完整代理 URL、token、账号 id；只输出出口 id/计数/分类。

---

## 七、逐项可行性裁决

| 项 | 裁决 | 规模/依据 |
|---|---|---|
| 单出口现状追踪 | **可行（已完成）** | 1.1-1.4 已列出全部读取点与传递方式 |
| browserless 按账号出口 | **可行（现有结构直接支持）** | `create_session` 已有 `account_id` 且 async（`engine_runtime.py:251-260`）；signer 已有 per-request `proxy`（`signer_client.py:344-374、556-583`；`main.go:501-522`）。S |
| browser 按账号出口 | **冲突/需新模块** | Chrome 代理是进程级（`browser_engine.py:206-208`），CDP 建 context 未传 proxy（`cdp_client.py:937-950`）；需 per-context proxy 或每账号浏览器进程。M-L |
| 契约 A（单端口按账号分流） | **需新模块，仅 browserless** | 账号身份只能塞 SOCKS5 用户名（`proxydial.go:49-59`）；需认证 SOCKS 前端。S-M，但可观测性差 |
| 契约 B（控制端点） | **推荐** | app S-M（resolver + canary + 事件），manager M-L；无 Go 改动 |
| 契约 C（分配文件） | **可行但降级** | 单向、无确认；适合 v0/缓存。S |
| 403 区分 region/verify | **需新增小模块** | `errors.py:277-294` 目前把 region 归普通 403；`routing_pipeline.py:959-1001` 会重签后切账号。S |
| canary 账号仲裁 | **需新增模块，可行** | 用 `enabled=false` 账号 + 现有 signer 会话；不占实例槽位，但占 Worker；需频率/超时/污染处理。S-M |
| 请求中换出口 | **需拍板** | 方案 1/2 为 S，方案 3（同账号重建槽位）为 M；必须遵守 `active_requests`/D-05（`AGENT.md:229-241`） |
| WARP 多身份 | **需新模块/运维** | 现脚本单配置单服务（`wireproxy-rotate.sh:46-53`）；N 份配置/N unit。M |
| fanout 接入 | **部分过于理想化** | 10 秒自动换节点（README《运维》）、root+TUN+netns、公共 VPN 质量、本地 DNS（README《已知限制》）；需禁用自动换节点/补只读 API。M-L |
| WARP+fanout 统一池 | **需新模块，且 fanout 有条件** | 统一出口注册表可行；fanout 的 IP 漂移与公网质量是硬约束 |
| 账号粘性持久化 | **可行（管理器侧）** | 绑定表 + HMAC；app TTL 缓存。S-M |
| 健康分层 | **可行** | L0-L3；L3 才可隔离。S |
| 每出口容量/速率 | **可行** | 不用 12MB；用账号数/并发/RTT/失败率；不得成为第二套准入账本。S-M |
| UI 只读状态 | **可行** | `SettingsView.vue:432-452` + `/config` 字段；不需 schema/设置项。S |
| env 默认无感 | **可行（有条件）** | 惰性模块 + 默认 `.env` 不含特殊变量；`env_file` 会全量注入（`docker-compose.yml:11-13`），不能靠 compose 白名单。S |
| 管理器不进分发 | **可行** | `.gitignore:12`、`.dockerignore:41`、Dockerfile `:200-205`。S |
| “所有 region 403 都换出口” | **过于理想化/前提待验证** | VPS 复盘显示历史 region 403 可能是裸 httpx 指纹而非出口（`VPS-OPS-NOTES.md:28-35`）；必须先证明 403 来自 signer 生成路径 |

### 7.1 明确“技术上做不到或代价过高”的部分

1. **传统 browser 引擎的 per-account 出口**：现有 Chrome 进程只有一个 `--proxy-server`，CDP 建 context 不传 proxy；要么改 CDP 参数（未验证版本支持/指纹影响），要么每账号一个浏览器进程（内存/风控不可接受）。
2. **“单端口按账号分流”作为通用方案**：只有 browserless + SOCKS5 用户名可行；Chrome 无法携带每账号认证；且把账号身份放进每次 SOCKS 连接，排障/审计成本高。
3. **fanout 作为“稳定出口”**：它自带 IP 漂移自动换节点，端口不变但 IP 变；不改造就不能满足“账号粘性、不主动轮换”。公网 VPN Gate 的 region/verify 风险也显著高于 WARP。
4. **用 12MB/35 分钟阈值做池分配**：这是轮换指标，不是出口质量/容量指标；且主动轮换与“确认 403 才换”冲突。
5. **把所有 region 403 都视为出口问题**：本仓已有反证；必须先分类“请求路径”与“探测路径”，否则会频繁换出口并放大 verify 风险。

---

## 八、最终形态 ASCII 图

### 8.1 默认模式（env 缺失，完全现状）

```text
┌──────────────────────────────────────────────────────────────────────┐
│ aistudio-api 进程                                                     │
│                                                                      │
│  Settings.proxy_url = socks5://172.17.0.1:40001                      │
│        │                                                             │
│        ├── BrowserlessEngineRuntime.create_session()                 │
│        │       └── BrowserlessSession(proxy=global)                  │
│        │               ├── mint_proof(proxy=global) ──┐              │
│        │               └── send_rpc(proxy=global) ────┤              │
│        │                                              ▼              │
│        │                                   waa-signer WorkerPool     │
│        │                                   (entry.proxy=global)      │
│        │                                              │              │
│        └── tier/cookie 探测（部分路径）── settings.proxy_url          │
│                                                       │              │
│  UI: SettingsView「已配置代理」只读                    │              │
└───────────────────────────────────────────────────────┼──────────────┘
                                                        ▼
                                       wireproxy (single identity)
                                                        │
                                                        ▼
                                                  WARP / Cloudflare
                                                        ▲
                                                        │
                              warp-health-check v2（12MB/35min 定时轮换）
                              wireproxy-rotate.sh（单配置重启）
```

### 8.2 特殊模式（`AISTUDIO_EXIT_MODE=external`，browserless）

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ aistudio-api 进程                                                             │
│                                                                              │
│  ExitResolver(env-gated) ── GET /v1/assign(hmac(account)) ──┐                │
│        │  TTL 缓存 / 失败回退 global                          │                │
│        ▼                                                     │                │
│  BrowserlessEngineRuntime.create_session(account_id)         │                │
│        └── BrowserlessSession(proxy=assigned_socks_url)      │                │
│                ├── mint_proof(proxy=exit)                    │                │
│                ├── send_rpc(proxy=exit)                      │                │
│                └── direct_http(proxy=exit)                   │                │
│                                                              │                │
│  RoutingPipeline.handle_error(AuthError)                     │                │
│        ├─ verify-account → 账号级：标记/切号（不换出口）      │                │
│        ├─ region 403 → CanaryAdjudicator（同一出口，canary 小号）│             │
│        │       ├─ exit_bad  → POST /v1/events + 换出口/重建   │                │
│        │       ├─ account_bad → 标记账号 + 切号               │                │
│        │       └─ inconclusive → 记录，不换出口               │                │
│        └─ 其他 403 → 记录（不换出口）                         │                │
│                                                              │                │
│  UI: SettingsView「外部出口池（只读）」                        │                │
└──────────────────────────────────────────────────────────────┼────────────────┘
                                                               ▼
                                            ┌──────────────────────────────┐
                                            │ 本地出口管理器（宿主/桥接）   │
                                            │ 127.0.0.1 / 172.17.0.1 + token│
                                            │                              │
                                            │ 绑定表: hmac(account) → exit │
                                            │ 出口表: warp-1/2.. fanout-1..│
                                            │ 健康: L0/L1/L2 + 事件/隔离    │
                                            └───────┬──────────────┬───────┘
                                                    │              │
                          ┌─────────────────────────┘              └─────────────────────┐
                          ▼                                                              ▼
              WARP 实例 1..N（wireproxy 多身份）                         fanout netns 出口 1..M
              每个独立配置/端口/密钥                                     每个 openvpn + 独立端口
              socks5://...:P1                                             socks5://...:Q1
                          │                                                              │
                          ▼                                                              ▼
                    Cloudflare WARP                                              VPN Gate 公共节点
                    （共享 anycast 出口）                                          （公网/家宽混合）
                          │                                                              │
                          └──────────────► 账号粘性绑定（管理器侧） ◄───────────────────┘
                                          不进 app 配置、不进分发包
```

### 8.3 事件时序（特殊模式）

```text
Client → app → BrowserlessSession(exit=E1) → upstream
                                   │
                                   └─ 403 [7,"Region not supported."]
                                          │
                                          ▼
                              classify: region_restricted
                                          │
                                          ▼
                    canary(account=C, exit=E1)  ── 成功 ──► account_bad
                                          │                    │
                                          │                    └─ 标记账号 + 切号；E1 保留
                                          └─ region 403 ──► exit_bad
                                                               │
                                                               ▼
                                            管理器: E1 quarantined
                                            迁移 E1 上所有账号 → E2/E3
                                            E2/E3 先 canary 验证
                                                               │
                                                               ▼
                                            当前账号按 active_requests 边界重建/失败
```

---

## 九、风险与未决问题

### 9.1 高优先级风险

1. **前提风险：region 403 的根因可能不是出口。** VPS 复盘（`VPS-OPS-NOTES.md:28-35`）显示同一出口上 signer 模型发现成功、裸 httpx tier 探测 403；如果把探测路径的 region 403 当成出口级，会频繁换出口并放大 verify。必须先保证“触发分类的 403 来自真实生成/signer 路径”，并让 canary 使用与真实请求一致的传输。
2. **canary 误判。** canary 成功只能证明“该出口对 canary 账号可用”，不能证明“对原账号可用”；canary 自身也可能被 region/verify 污染。需要固定低价值 Free 账号、频率上限、污染后停用、inconclusive 不切换。
3. **fanout 的 IP 漂移。** 10 秒健康检查 + 连续两次不符自动换节点（README《运维》）会在端口不变的情况下改变出口 IP，直接破坏账号粘性；不关闭/改造就不能用于本方案。
4. **fanout 的部署与质量。** root、netns、`/dev/net/tun`、公共 VPN Gate 节点满员/下线、机房特征、本地 DNS 解析（README《安装》《已知限制》）都可能让“多一个出口”变成“多一个不稳定源”。
5. **请求中换出口的身份突变。** 同一账号在同一个请求里从 E1 换到 E2，可能触发 Google 的 IP 跳变风控；方案 1（session 代理热切换）必须单独拍板，默认应选方案 2/3（失败或重建槽位）。
6. **管理器单点与容器网络。** 容器内访问宿主控制端点需要桥接地址/端口/token 或 unix socket bind mount；管理器挂掉时回退全局 socks，可能回到同一个坏出口，表现为“降级但可观测”。
7. **env 泄漏面。** “默认分发无感”依赖默认 `.env` 不含特殊变量；compose 的 `env_file` 会全量注入用户 `.env`，`docker inspect`、`/proc/*/environ`、signer 子进程继承（`local_daemon.py:121-126`）也可见 token/HMAC 盐；日志不得打印完整代理 URL。
8. **第二套并发账本。** 管理器为了分配而统计每出口 active 数时，不能变成请求准入；app 的 `active_requests` 仍是唯一准入原语（`AGENT.md:229-233`）。出口隔离只能影响“分配/换身份时机”。

### 9.2 未决问题（需用户拍板）

1. 特殊模式是否**只支持 browserless**？如果必须支持 browser 引擎，per-account 出口的代价（CDP 改造或每账号进程）是否接受？
2. 请求路径遇到确认的出口级 403 时，当前请求是**直接失败**、**同账号换出口重建槽位**，还是**热切换 session 代理后重试**？
3. canary 账号选哪个（必须是低价值 Free、`enabled=false`）、允许的探测频率、超时、失败后的停用策略。
4. “canary 成功但原账号 region 403”时，账号标记用什么语义：复用 `verify_required`、新增 `region_blocked`，还是只记 `auth_errors` 不锁账号？这会影响 UI/调度/保活。
5. fanout 是否允许改造（关闭自动换节点、补只读 API）？如果不允许，fanout 只能作为人工备用出口，不能进自动池。
6. 管理器运行位置（VPS 宿主 / 容器 sidecar / 本机）与控制端点形态（HTTP 桥接 vs unix socket）；token 如何注入且不被 signer 子进程继承。
7. 出口隔离后是否迁移该出口上的**所有**账号，还是只迁移当前账号？迁移全部更一致，但会造成一次集中 IP 变更。
8. 是否允许在 UI 只读卡片展示出口数量/健康度；是否连“最近切换时间”也不展示，以避免暴露运维节奏。

### 9.3 与 D-01..D-14 / §4 红线的对照结论

- 不新增实例、不改变 Free/Pro lane、不改 tier 选择器：出口池只影响每个 session 的 socks 端点，不改变实例定义（`AGENT.md:198-228`）。
- 不引入第二套并发账本：`active_requests` 仍是唯一准入；管理器统计只做分配提示（`AGENT.md:229-233`）。
- 不在旧 Page/Context 未 drain 时先建新页：browserless 无 Page/Context，但换出口重建仍应走“释放句柄 → active_requests==0 → 关闭 → 重建”的 D-05 形态（`08-09-decision-record.md:17`；`AGENT.md:236-241`）。
- 不做定时/流量主动轮换：出口切换只由“确认出口级 403”触发；现有 12MB/35 分钟逻辑不能进新池（`warp-health-check-v2.sh:42-45、203-229`）。
- 不做后台自动 rebind / rebalance：出口迁移必须是请求触发或显式运维动作；不能恢复已禁止的后台机制（`AGENT.md:236-241、266-268`）。
- 不新增 config schema/UI 设置项：`AISTUDIO_EXIT_*` 是环境变量门控；UI 只读（D-12 口径）。
- 不读账号 Cookie 到管理器：canary 在 app 侧执行；管理器只拿 HMAC 标识（`AGENT.md:250-257` 的实机防风控边界也要求不扩大凭据暴露面）。
