# REV-2.1.5.12 — fanout 机器接口（commit 29aa7af）只读审查

- 审查日期：2026-11-23
- 审查角色：独立只读 Reviewer（100% 只读）
- 审查对象：`D:\LLM\self_programming\fanout` commit `29aa7af8f9069102de86465ab0228b77cfa75754`
  - `feat(machine): loopback token 机器接口（补出口/换节点/读状态）`
  - 变更：`machine.go`（+495）、`machine_test.go`（+520）、`main.go`（+11）、`README.md`（+63），共 4 文件 1089 行新增
- 工作区事实：fanout 工作树干净，`HEAD=29aa7af`；未 push
- 写入边界：本轮只在 aistudio-api 仓库写入本文件；aistudio-api 工作区其它既有未提交改动与本审查无关，未触碰
- 结论：**CONDITIONAL**
- 依据：commit 实际 diff、`machine.go` / `machine_test.go` / `main.go` / `readonly.go` / `auth.go` / `basepath.go` / `manager.go` / `provision.go` / `panel_xcl.go` / `README.md` 源码，以及本轮 Linux 交叉 build/vet/test 编译实跑

## 一、结论

**CONDITIONAL（不可写「可进入联调」）。**

核心 handler 层没有发现 P0、认证绕过或凭据外泄：

- Bearer 常量时间比较、空 token 不启用、loopback 来源判断（`127.0.0.1` / `127.x` / `::1`）、不采信 `X-Forwarded-For` 均成立；
- 固定窗口 30/分钟、429 + `Retry-After`、限流器互斥均成立；
- 方法白名单、405 + `Allow`、未知端点 404、POST 4 KiB 上限、slot/region/count/template 校验大体成立；
- status 只回白名单字段，机器接口按设计返回含凭据的 `socks_url`，`/api/readonly/*` 语义未被修改；
- provision/swap/stop/start 直接复用现有 `Manager` 方法，未复制核心业务逻辑；
- 审计日志不落 token、Authorization、请求头或响应体；
- `f93a39a` 的 mark/readonly 改动未被本 commit 触碰，回归风险低。

但仍存在 **1 个 P1 + 4 个 P2**，因此当前不写「可进入联调」：

1. **P1-1**：`FANOUT_MACHINE_TOKEN` 未设置时，`main` 根本不安装 `machineHandler`，`machine.go` 里「空 token 一律 404」的分支是生产不可达的死代码；base path 为空时 `/api/machine/status` 实际返回管理面的 **401**，与 README「未设置时接口整体不存在（404）」直接矛盾，且单测因直接调用 wrapper 而给出虚假通过。
2. **P2-1**：token 太短的 `log.Fatalf` 发生在隧道恢复与健康检查启动之后，会绕过 `defer unlock()` 与 `mgr.Shutdown()`，可能留下子进程/netns。
3. **P2-2**：限流是单进程单桶，没有按来源隔离；且 loopback 检查在限流之前，非 loopback 请求绕过限流并逐条写审计日志，默认监听 `0.0.0.0:8899` 时可被公网放大日志/CPU。
4. **P2-3**：`start` 同时收到 `host` 与 `slot` 时静默忽略 `slot`，与 README「start 没有 slot 参数」不一致，集成方可能误以为按 slot 启动。
5. **P2-4**：`stop` 的「先 `machineHasSlot` 再 `m.Stop`」存在并发竞态，槽位刚好被另一请求停掉时返回 **500** 而不是 404；`swap` 已经做了重查，`stop` 没有对齐。

修完 P1-1（并建议同轮处理 P2-1..P2-4）后可进入联调。

## 二、必查项逐条结论（1–9）

> 记号：PASS=符合；P1/P2=缺陷；Q/N=观察/提示。

### 1. 默认关闭（未设置 token 时 `/api/machine/*` 是否完全 404；token <16 是否拒绝启动）

- **P1（默认关闭语义未落到生产接线）**：
  - `main.go:182-190` 先读 `machineTokenFromEnv`，但只有 `if machineTok != ""` 才执行 `handler = machineHandler(...)`。token 未设置时，`machineHandler` 的「空 token → 404」分支（`machine.go:141-152`）根本不会被安装。
  - base path 为空时（设置面板允许，`main.go:367-371` → `basepath.go:44-68`），`StripBasePath` 直接透传（`basepath.go:120-123`），`auth.Wrap` 对 `/api/` 前缀返回 `401 {"error":"未登录"}`（`auth.go:160-162`），不是 404。
  - 默认随机 base path 下，裸 `/api/machine/status` 会碰巧被 `StripBasePath` 以 404 拒绝（`basepath.go:125-134`）；但带已知 base path 的 `/<base>/api/machine/status` 仍会走到 `auth.Wrap` 得到 401。也就是说 404 不是由机器接口逻辑保证，而是由 base path 配置碰巧决定。
  - `README.md:162` 的「未设置时接口整体不存在（404）」在当前 `main` 组装下不总成立。
  - `machine_test.go:192-204` 的 `TestMachineEmptyTokenNeverAuthenticates` 直接调用 `machineHandler(passthrough(), newFakeMachine(), "")`，因此测试通过，但没有覆盖真实 `main` 接线；这是 P1-1 未被测试发现的直接原因。
- **PASS（token <16 拒绝启动）**：`machineTokenFromEnv` 在 `main.go:182-185` 被调用，错误经 `log.Fatalf` 退出；`machine_test.go:123-148` 覆盖空串、15 字符、6 个多字节字符、16 字符、两端空格。功能上满足「拒绝启动」，但校验时机问题见 P2-1。
- **Q-6**：若按 P1-1 建议无条件安装 wrapper，当前空 token 分支返回 `{"error":"机器接口未启用"}`（`machine.go:145`），仍会泄露「机器接口」这一功能概念；应改为与其它未知路径完全一致的普通 404。

### 2. 鉴权（Bearer 常量时间、空 token、loopback、X-Forwarded-For）

- **PASS（Bearer 常量时间比较）**：`machine.go:195` 调用 `tokenMatches`；`readonly.go:80-84` 先对两端做 SHA-256 再 `subtle.ConstantTimeCompare`，长度不同也不会提前返回。
- **PASS（空 token 永不放行）**：生产未设置 token 时不会安装机器接口（`main.go:186-190`）；即使安装，`machineHandler` 的空 token 分支固定 404（`machine.go:141-150`）。未发现空 Bearer 可通过的生产路径。
- **PASS（loopback）**：`isLoopbackRequest`（`readonly.go:88-95`）只读 `RemoteAddr`，`net.ParseIP(...).IsLoopback()` 覆盖 `127.0.0.1`、`127.x`、`::1`、IPv4-mapped `::ffff:127.0.0.1`；解析失败一律 false（fail closed）。`machine_test.go:187-189` 覆盖 `[::1]`，`readonly_test.go:85-102` 覆盖 `127.0.0.1` / `127.0.0.2` / 公网 IPv4 / 公网 IPv6 / 垃圾串。
- **PASS（X-Forwarded-For 不得绕过）**：代码完全不读 `X-Forwarded-For`、`X-Real-IP` 等头（`readonly.go:86-95`），因此无法用该头伪造 loopback。测试未覆盖该负例，见 N-1。
- **PASS（非 loopback 拒绝）**：`machine.go:173-177` 在鉴权前返回 403；`machine_test.go:184-186` 用 `192.0.2.10:1234` 断言 403。
- **Q-1（安全假设）**：`RemoteAddr` 只看 TCP 对端。README 建议公网部署套反代；若 nginx 与 fanout 同机，外部请求经反代后 `RemoteAddr` 恒为 `127.0.0.1`，loopback 这一层不再代表「原始客户端在本机」，机器接口只靠 Bearer token 保护。详见 Q-1。
- **Q-5（潜在 footgun）**：`newMachineHandler`（`machine.go:154`）本身不校验 `token == ""`；`tokenMatches("", "")` 会返回 true（`readonly.go:80-84`）。当前只由 `machineHandler` 的 `token == ""` 分支保护；若后续重构直接调用 `newMachineHandler` 且传空 token，会出现空 Bearer 放行。建议在 `newMachineHandler` 或 `tokenMatches` 里显式拒绝空 `want`。

### 3. 限流（固定窗口 30/分钟、隔离、429 Retry-After、并发竞态）

- **PASS（固定窗口 30/分钟）**：`machineRateLimit=30`、`machineRateWindow=time.Minute`（`machine.go:23-25`）；`allow` 在 `windowStart` 为零或超过窗口时清零（`machine.go:108-121`）。
- **PASS（429 + Retry-After）**：超限时 `machine.go:181-192` 设置 `Retry-After`，返回 JSON `{"error":"请求过于频繁","retry_after":N}`；`machine_test.go:285-315` 真实发送 31 次并断言 429、非空 `Retry-After`、`retry_after >= 1`、JSON 可解析。
- **PASS（并发无竞态）**：限流状态由 `sync.Mutex` 保护（`machine.go:91,105-106`），`allow` 内无第二套账本；`TestMachineRateLimiter`（`machine_test.go:150-172`）覆盖窗口重置。未做 `-race` 实跑，见 N-2。
- **P2（隔离与旁路）**：限流是单个 `machineRateLimiter` 实例、单桶，不按来源 IP 隔离；且 loopback 检查（`machine.go:173-177`）在 `rl.allow()`（`machine.go:181`）之前，非 loopback 请求完全绕过限流，却逐条调用 `machineAudit`（`machine.go:174`）。详见 P2-2。
- **Q**：固定窗口在窗口边界允许最多约 2×30 的突发；这是固定窗口语义，不是缺陷，但需知悉。

### 4. 方法 / 路径 / 请求体 / 参数校验

- **PASS（方法白名单）**：`machineRoutes` 只登记 5 个端点（`machine.go:234-242`）；未登记路径在鉴权后返回 404（`machine.go:202-206`）；方法不符返回 405 并带 `Allow`（`machine.go:208-215`）。`machine_test.go:206-228` 覆盖 GET provision → 405、POST status → 405、未知端点 → 404。
- **PASS（未知端点不泄露）**：鉴权在路由查找之前（`machine.go:195-206`），无 token 时未知端点返回 401 而不是 404，避免端点枚举。
- **PASS（POST 请求体 4 KiB）**：`machineMaxBodyBytes=4<<10`（`machine.go:28`）；POST 路由在进入业务前调用 `machineReadBody`（`machine.go:217-220,246-264`），`http.MaxBytesReader` + `io.Copy(io.Discard)`，超限 400。`machine_test.go:467-480` 用 4097 字节断言 400 且未进入 `Provision`。
- **PASS（region/count/template/slot）**：`normalizeMachineRegion` 只接受空或两位 ASCII 字母（`machine.go:448-462`）；count 1..50（`machine.go:312-317`）；template 非负（`machine.go:324-331`）；slot 从 1 开始（`machine.go:424-431`）。`machine_test.go:317-360,377-426` 覆盖非法 region/count/template/slot 与合法路径。
- **P2-3（start 静默忽略 slot）**：`machineStart` 只在 `host == ""` 时拒绝 `slot`（`machine.go:387-395`）；`host` 非空时 `slot` 被忽略。详见 P2-3。
- **P2-4（stop 竞态映射错误）**：`machineStop` 先 `machineHasSlot` 再 `m.Stop`（`machine.go:371-376`），并发停同一槽位时后到的 `m.Stop` 返回「槽位不存在」，handler 却返回 500。详见 P2-4。
- **Q**：4 KiB 只对 POST 生效（`machine.go:217`）；GET 请求体不被读取/限长。端点只用 query，风险低，但若严格按「请求体 4 KiB」验收，需说明或对全部方法生效。
- **Q**：`HEAD /api/machine/status` 返回 405（`machine.go:208-215`）；若外部健康检查惯用 HEAD，可考虑允许 HEAD。

### 5. 数据边界（status 白名单、socks_url、readonly、不得回显 .ovpn/password/state.json、审计日志）

- **PASS（status 只回白名单）**：`machineExit` 只含 slot/port/status/exit_ip/country/country_code/suspect/socks_url（`machine.go:65-74`）；`machineStatusView` 显式映射这些字段（`machine.go:284-295`），不序列化 `Tunnel` / `Node` 本体。
- **PASS（机器接口允许 socks_url 含凭据）**：`SocksURL: socksURL(host, t.Port, t.credential())`（`machine.go:293`）；`socksURL` 在 `cred.User != ""` 时拼 `socks5://user:pass@host:port`（`cred.go:83-88`）。`machine_test.go:255-279` 断言凭据出现在 `socks_url` 且 `secret-config-line` / `remote 1.2.3.4` / `.ovpn` / `state.json` / `password` / `socks_user` / `socks_pass` 不出现。
- **PASS（readonly 仍不含凭据）**：本 commit 未修改 `readonly.go`；`readonlyTunnel` 仍只有 slot/port/status/exit_ip/country/country_code/suspect（`readonly.go:19-27`），`readonlyTunnels` 不取 `credential()`（`readonly.go:41-56`）。`readonly_test.go:28-59` 断言 `secret-user` / `secret-pass` / `cred` / `ovpn` 不出现。
- **PASS（不回显 .ovpn / Node.Config / state.json）**：status 走白名单映射；机器接口错误来自 `m.Provision` / `m.Swap` / `m.Stop` / `m.Start` 的通用错误，未发现会拼接 `Node.Config` 或 `state.json` 的路径；panic 恢复只返回「内部错误」（`machine.go:166-171`）。
- **PASS（审计日志不含 token / Authorization）**：`machineAudit` 只记时间（log 包）、来源、动作、目标、结果码（`machine.go:471-477`）；不读 `Authorization`，不记请求头/请求体/响应体。`machine_test.go:482-514` 捕获 `log` 输出，断言 `机器接口`、`动作=status`、`结果=200`、`动作=swap`、`结果=404` 存在，token 与 `Authorization` 不存在。
- **Q-7**：panic 日志里的 `详情=%v` 未经过 `sanitizeAudit`（`machine.go:168`）；`sanitizeAudit` 的 80 字节截断可能切断 UTF-8 rune（`machine.go:491-493`）。当前 panic 值来自运行时的概率极低，属低风险加固项。

### 6. 复用（provision/swap/stop/start 是否直接调 Manager；xray-cf-lite template）

- **PASS（直接复用 Manager）**：
  - provision → `m.Provision(ProvisionRequest{...})`（`machine.go:337`；`provision.go:24-60`）；
  - swap → `m.Swap(slot)`（`machine.go:354`；`manager.go:315-334`）；
  - stop → `m.Stop(slot)`（`machine.go:374`；`manager.go:288-305`）；
  - start → `m.Nodes()` 精确匹配 `HostName` 后 `m.Start(picked)`（`machine.go:398-411`；`manager.go:74-109`），与 `apiStart`（`main.go:245-266`）同源。
  - 没有复制隧道创建/换节点/停隧道/批量开出口的核心逻辑；仅 status 映射和 node 查找有少量重复。
- **PASS（xray-cf-lite 下 template）**：`machineProvision` 在 `m.BackendName() == "xray-cf-lite"` 时把 `tpl` 置 0（`machine.go:332-335`）；`machine_test.go:362-375` 用 fake backend 断言传给 `Provision` 的 `TemplateID == 0`。`BackendName` 实现为 `openPanel().Kind()`（`machine.go:51-57`）。
- **Q-3**：`provision` 返回 `{"job":"..."}`，但机器接口没有 `/api/machine/jobs`，管理器无法读取 job 进度/失败原因；也没有 `/api/machine/nodes`，`start?host=` 需要带外节点名。功能上可通过轮询 status 观察出口，但 job 字段基本不可用。建议补只读 job/nodes 端点或文档说明。
- **Q-4**：`start` 用 `m.Nodes()` 原表匹配，不经过 `nodePoolLocked()`；当「只用家宽」开启时，`start` 仍可启动机房节点（`manager.go:253-264` 的过滤只在 `pickNodes` 生效）。UI 的 `apiStart` 同样如此，不是本 commit 新增，但机器接口会放大这个不一致。
- **Q-11**：XCL 的 template 判断在 handler 层做；若 `openPanel()` 瞬时失败，`BackendName()` 返回空串（`machine.go:51-57`），`tpl` 不会被清零，随后 `runProvision` 里对 XCL 调 `CloneToTunnels` 会得到 `errXCLReadOnly`（`panel_xcl.go:382-384`），job 失败但隧道已建。建议把「XCL 忽略 template」下沉到 `Manager.Provision`/`runProvision`，或在该处重新判定 backend。

### 7. 回归（原有 UI / 只读接口 / 健康逻辑 / fork 的 mark/readonly）

- **PASS**：本 commit 只新增 `machine.go` / `machine_test.go`，对 `main.go` 仅加 11 行，`README.md` 加机器接口章节；未修改 `readonly.go`、`health.go`、`tunnel.go`、`manager.go` 的既有逻辑。
- **PASS**：`machineHandler` 对非 `/api/machine/` 路径原样 `next.ServeHTTP`（`machine.go:156-159`），`readonlyHandler` 仍作为内层；启用机器接口不会改变 `/api/readonly/*` 的鉴权、loopback、GET-only 语义。
- **PASS**：`Manager.BackendName` 是纯新增方法（`machine.go:51-57`），未与现有方法冲突；Linux 交叉 build 通过。
- **PASS**：`f93a39a` 的 mark 模式与 readonly 接口未被本 commit 触碰；`readonly_test.go` / `health_test.go` 未修改，Linux 测试二进制编译通过。
- **PASS（编译级）**：`GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build ./...`、`go vet ./...`、`go vet -tests ./...`、`go test -c` 均通过；未发现新增符号冲突或测试编译错误。

### 8. 测试质量（13 个用例是否真实断言；harness 是否与生产同源；未覆盖的真实 Linux 行为）

- **PASS（用例数量与真实断言）**：`machine_test.go` 共 13 个 `Test*`：
  1. `TestMachineTokenFromEnv`：空/空白/15 字符/多字节不足/16 字符/两端空格，断言错误与返回值；
  2. `TestMachineRateLimiter`：2 次放行、第 3 次 429 等待 1m、窗口重置；
  3. `TestMachineHandlerAuthAndLoopback`：无 token 401、错 token 401、非 loopback 403、`[::1]` 200；
  4. `TestMachineEmptyTokenNeverAuthenticates`：空 token 404、任意 token 404、非机器路径透传；
  5. `TestMachineHandlerMethods`：GET provision 405 + `Allow: POST`、POST status 405 + `Allow: GET`、未知端点 404；
  6. `TestMachineStatusShape`：backend/counts/字段/suspect/socks_url/凭据出现/敏感串不出现/Cache-Control；
  7. `TestMachineRateLimitHTTP`：31 次真实 HTTP 调用、429、Retry-After、JSON；
  8. `TestMachineProvisionValidation`：9 个非法参数 400 且不调用 `Provision`、合法参数传给 Manager；
  9. `TestMachineProvisionIgnoresTemplateInXCL`：XCL 下 `TemplateID == 0`；
  10. `TestMachineSwapAndStop`：非法 slot 400、不存在 404、成功调用记录、Swap/Stop 错误码；
  11. `TestMachineStart`：缺 host 400、只给 slot 400、未知 host 404、成功 slot/port、Start 错误 400；
  12. `TestMachineBodyLimit`：4097 字节 400 且不进入业务；
  13. `TestMachineAuditLogHasNoToken`：审计日志内容、token/Authorization 不出现。
  以上均有实际 `if`/`Fatalf` 断言，不是只调用不断言。
- **PASS（harness 与生产同源）**：测试直接调用真实的 `machineHandler` / `newMachineHandler` / `machineTokenFromEnv` / `machineRateLimiter` / `machineStatusView` / `machineRoutes` / `machineReadBody` / `machineAudit` / `sanitizeAudit`，以及真实的 `tokenMatches` / `isLoopbackRequest` / `socksURL` / `Tunnel` / `Node`；`fakeMachine` 只替代 `Manager` 的 8 个方法，没有逐字复制生产 handler 逻辑。对 handler 层来说，这不是易漂移的「复制式 harness」。
- **P1-1 根因（接线未覆盖）**：测试没有覆盖 `main.go` 的真实组装——即「token 为空时是否安装 machineHandler」。`TestMachineEmptyTokenNeverAuthenticates` 直接构造 `machineHandler(..., "")`，所以它验证的是 wrapper 自身，而不是生产路径；这正是 P1-1 漏检的原因。建议抽出 `buildHandler(...)` 或在 `main` 包内加一个组装级测试。
- **N-1（未覆盖的真实行为）**：
  - `X-Forwarded-For: 127.0.0.1` 从非 loopback 来源不得放行（代码不读该头，但无测试锁死）；
  - `base_path` 为空/已知 base path 下，未设置 token 的 `/api/machine/*` 真实状态码；
  - 非 loopback 请求是否计入限流、是否产生审计日志（P2-2）；
  - 限流器并发调用（`-race`）；
  - `GET` 带超大 body、`HEAD` 行为；
  - `start?host=...&slot=...` 同时出现的校验；
  - 两个并发 `stop` 的 404/500 竞态（P2-4）；
  - 真实 `Manager.BackendName()` / `openPanel()` / XCL 探测（测试用 fake）；
  - 真实 `hostPublicIP()` 探测与缓存（测试用 `setPublicIPOverride`）；
  - 真实 `http.Server` 下的 `MaxBytesReader`、chunked body、连接复用；
  - 本机反代场景下 `RemoteAddr` 恒为 loopback 的边界（Q-1）。
- **N-2（平台限制）**：仓库全局使用 `golang.org/x/sys/unix` 的 `Flock` / `Setns`，Windows 原生 `go test` 无法编译（见第五节）；本轮只做了 Linux 交叉 build/vet/test 编译，未执行 Linux 测试二进制。13 个用例的运行时断言未在本机实跑，结论来自源码与编译检查。
- **N-3（fake 的局限）**：`fakeMachine.Provision` 直接返回 `f.jobs.New(...)`，不执行真实 `Manager.Provision` 的异步 `runProvision`；`fakeMachine.Start` 返回固定 slot 5/port 1200，不执行真实隧道创建。对 handler 层足够，但不能替代 Manager 集成测试。
- **N-4**：`machineMinTokenLen` 按 `utf8.RuneCountInString` 计「字符」而非字节；16 个非 ASCII rune 也会通过。若希望按熵/字节约束，需明确文档；当前与 README「16 个字符」一致。

### 9. 实跑

- **PASS（Linux 交叉 build/vet）**：见第五节，全部 exit 0。
- **PASS（测试编译）**：`GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go test -c` exit 0，说明 13 个用例与测试依赖在 Linux 目标下可编译。
- **未能实跑（平台限制）**：Windows 原生 `go test` 因仓库全局使用 `unix.Flock` / `unix.Setns` 无法构建；没有 Linux 运行环境，且按红线不启动服务/二进制，因此未执行 Linux 测试二进制。详见 N-2。

## 三、P0/P1/P2 明细

### P1-1 默认关闭的 404 语义未落到生产接线；empty-token 404 是死代码

- 位置：`fanout/main.go:182-190`、`fanout/machine.go:141-152`、`fanout/auth.go:160-162`、`fanout/basepath.go:118-135`、`fanout/README.md:162`、`fanout/machine_test.go:192-204`
- 事实：
  - `main` 只在 `machineTok != ""` 时执行 `handler = machineHandler(...)`（`main.go:186-190`）；未设置 token 时不会安装机器接口层。
  - `machineHandler` 的 `token == ""` 分支（`machine.go:141-150`）只在测试直接调用时可达。
- 复现（按代码路径；未启动真实服务）：
  1. 通过设置面板把 `base_path` 设为空（`main.go:367-371` → `setBasePath("")`，`basepath.go:44-68`），不设置 `FANOUT_MACHINE_TOKEN`；
  2. 请求 `GET /api/machine/status`；
  3. `StripBasePath` 在 base 为空时直接透传（`basepath.go:120-123`），`auth.Wrap` 看到 `/api/` 前缀且无会话，返回 `401 {"error":"未登录"}`（`auth.go:160-162`），不是 404。
  4. 默认随机 base path 下，裸 `/api/machine/status` 会被 `StripBasePath` 以 404 拒绝（`basepath.go:125-134`）；但带已知 base path 的 `/<base>/api/machine/status` 仍会得到 401。404 不是由机器接口保证的。
- 影响：直接违反必查项 1 与 README 的默认关闭承诺；测试因直接调用 wrapper 而虚假通过；不构成认证绕过或数据泄露，但属于验收语义失败。
- 建议：
  - 将 `machineHandler` 无条件安装（把 `main.go:187` 移出 `if`），空 token 时返回与其它未知路径一致的普通 404；不要返回含「机器接口未启用」字样的 JSON（见 Q-6）。
  - 若希望 `/api/machine`（无尾斜杠）也不落到管理面 401，一并覆盖该精确路径。
  - 抽出 `buildHandler` 或加一个组装级测试，锁定「token 未设置 → `/api/machine/*` 404」在生产 `main` 路径成立。

### P2-1 token 长度校验发生在隧道恢复之后，short token 的 Fatal 会留下子进程/netns

- 位置：`fanout/main.go:90-99`（`restoreState` + `go mgr.WatchHealth()`）、`fanout/main.go:182-185`（`machineTokenFromEnv` + `log.Fatalf`）
- 事实：`restoreState` 对每条持久化隧道执行 `go m.restoreTunnel(t)`（`state.go:89-121`），健康检查也已启动；token 太短的 `log.Fatalf` 调用 `os.Exit(1)`，绕过 `defer unlock()`（`main.go:58`）与信号处理里的 `mgr.Shutdown()`（`main.go:101-110`）。
- 复现：准备一个含隧道的 `state.json`，设置 `FANOUT_MACHINE_TOKEN=short` 启动；进程会先恢复隧道/启动 goroutine，再因 token 太短退出，可能留下 openvpn/Xray 子进程与 netns。
- 影响：误配置反复重启会积累孤儿进程/网络资源；虽不涉及安全边界，但属于启动可靠性问题。
- 建议：在 `main` 最前面（flag 解析后、`initMainNetns`/`NewManager`/`restoreState` 之前）校验 `FANOUT_MACHINE_TOKEN`；若必须在后面校验，则 fatal 前显式 `mgr.Shutdown()`、`closePanel()`、`unlock()`。

### P2-2 限流是单进程单桶，未按来源隔离；非 loopback 请求绕过限流并逐条审计

- 位置：`fanout/machine.go:88-122`（单 `machineRateLimiter` 状态）、`fanout/machine.go:173-193`（loopback 检查先于 `rl.allow()`）、`fanout/machine.go:174`（403 审计）、`fanout/settings.go:20,67,139-141`（默认监听所有网卡）
- 事实：
  - `machineRateLimiter` 只有一组 `windowStart/count`，没有按 token 或来源 IP 的键；当前只有一个 token，所以「每 token」是构造上成立，但「每来源」不成立。
  - `!isLoopbackRequest` 直接 `machineAudit(..., 403)` 并返回（`machine.go:173-177`），不会执行 `rl.allow()`；因此非 loopback 请求完全绕过限流。
- 复现：
  - 本机 DoS：任意本机进程在 1 分钟内发 30 个请求（哪怕全部是无 token/未知路径）即可耗尽桶，合法管理器随后收到 429（`machine.go:181-192`）。
  - 公网日志放大：机器接口启用且管理端口按默认 `:8899` 暴露时，从非 loopback 反复请求 `/api/machine/status`，每次得到 403 并写一条审计日志（`machine.go:174`），限流器不介入；可放大 CPU/日志/磁盘。
- 影响：限流不能保护「猜对 token 的人」之外的本地公平性；非 loopback 侧还新增了可被公网放大的日志面。
- 建议：
  - 在 loopback 判断前加一个按来源 IP 的轻量限流（或对非 loopback 直接返回通用 404 且不写审计）；
  - 将主限流改为按 token + 来源键控（注意 map 清理），或把机器接口绑定到独立 loopback 监听/Unix socket，确保公网请求根本到不了；
  - 增加「非 loopback 不计入/不放大审计」的测试。

### P2-3 `start` 在 host 存在时静默忽略 slot

- 位置：`fanout/machine.go:385-395`
- 事实：`machineStart` 取 `host`；只有 `host == ""` 时才检查 `slot` 并返回 400。若 `host` 非空且同时带 `slot`，`slot` 被静默忽略，实际由 `m.Start` 分配第一个空闲槽位。
- 复现：`POST /api/machine/start?host=node-1&slot=3`（合法 token、loopback）→ 返回 200，`slot` 是服务端实际分配的槽位（例如 5），不是 3；响应会带实际 slot，但请求中的 slot 没有任何报错。
- 影响：集成方若用 slot 表达「在指定槽位启动」会得到不同结果；与 README「start 没有 slot 参数，槽位由服务端分配」不一致。
- 建议：只要 `slot` 出现就拒绝（无论 host 是否为空），返回 400 并说明「start 不接受 slot」；或在 README 明确「两者同时出现时 slot 被忽略」。补测试。

### P2-4 `stop` 的并发竞态把 404 变成 500

- 位置：`fanout/machine.go:364-378`（`machineHasSlot` 371-373，`m.Stop` 374-376）；对比 `machineSwap` 的竞态处理 `machine.go:351-359`
- 事实：`machineStop` 先 `machineHasSlot` 再 `m.Stop`。两个并发请求同时通过预检后，第一个 `m.Stop` 删除槽位；第二个 `m.Stop` 返回「槽位 N 没有运行中的隧道」，handler 直接返回 500（`machine.go:374-376`）。
- 复现：对同一现存 slot 并发发两个 `POST /api/machine/stop?slot=N`；其中一个 200，另一个可能 500。
- 影响：违反 README「404 槽位不存在」的错误映射；客户端可能把 500 当作内部故障重试。
- 建议：与 `machineSwap` 对齐——`m.Stop` 返回错误后重查 `machineHasSlot`，不存在则 404，否则 500；补并发测试。

## 四、Q/N

- **Q-1（部署假设：同机反代让 loopback 不再是边界）**：`isLoopbackRequest` 只看 `RemoteAddr`（`readonly.go:88-95`），这是正确的实现；但 README 建议公网部署套反代，且 fanout 默认监听所有网卡（`settings.go:20,67,139-141`）。同机 nginx 转发后所有外部请求的 `RemoteAddr` 都是 `127.0.0.1`，机器接口（比只读接口权限更大）会变成公网可达，只靠 Bearer token。建议在机器接口文档中明确「反代必须屏蔽 `/api/machine/*`」或把机器接口改为独立 loopback 监听/Unix socket；否则「仅本机」是运维假设而非网络边界。
- **Q-2（status 可能阻塞在公网 IP 探测）**：`machineStatus` 每次调用 `hostPublicIP()`（`machine.go:266-271`）；`hostPublicIP` 在无 override/缓存时最多串行探测 3 个 URL，每个 5s（`publicip.go:11-16,67-80`），失败没有负缓存。若机器接口被高频轮询且母机出网异常，status 可能每次阻塞约 15s 并反复拉起 curl；`http.Server` 未设置读写超时（`webserver.go:52`）。这是既有 UI 也有的模式，但机器接口更容易被轮询。建议增加负缓存/短 TTL，或在机器 status 中优先使用 `-ip`/缓存，探测失败直接回退 `127.0.0.1`。
- **Q-3（provision job 与 nodes 不可读）**：`provision` 返回 `{"job":"..."}`（`machine.go:337-341`），但机器接口没有 `/api/machine/jobs`；`JobStore.Views` 只在管理面 `/api/jobs`（`main.go:481-485`）暴露。`start` 需要 host（`machine.go:398-409`），但机器接口没有节点列表。管理器只能轮询 status 观察出口，job 字段和 `start` 的节点发现都缺少机器面入口。建议补只读 `GET /api/machine/jobs?id=` 与 `/api/machine/nodes`，或在 README 说明「provision 只需触发，进度看 status」。
- **Q-4（start 不受「只用家宽」过滤）**：`machineStart` 用 `m.Nodes()` 原表（`machine.go:398`），不经过 `nodePoolLocked()`（`provision.go:253-264`）；开启「只用家宽」时仍可按 host 启动机房节点。`apiStart` 也有同样行为，不是本 commit 新增，但机器接口会放大这个不一致。建议在 `machineStart` 复用 `nodePoolLocked()` 或明确文档。
- **Q-5（潜在空 token footgun）**：`newMachineHandler` 不检查 `token == ""`（`machine.go:154`）；`tokenMatches("", "")` 为 true（`readonly.go:80-84`）。当前 `machineHandler` 的空 token 分支挡住了，但后续重构若直接调用 `newMachineHandler` 会放行空 Bearer。建议在 `newMachineHandler` 或 `tokenMatches` 中拒绝空 `want`，并加单测。
- **Q-6（空 token 404 响应体泄露功能名）**：`machine.go:145` 返回 `{"error":"机器接口未启用"}`；若按 P1-1 无条件安装 wrapper，这仍会让探测者知道「机器接口」存在。建议返回普通 `http.NotFound` 或与其它未知路径完全一致的响应。
- **Q-7（审计/panic 日志卫生）**：`machineAudit` 已 sanitize；但 panic 日志 `详情=%v` 未 sanitize（`machine.go:168`），`sanitizeAudit` 按字节截断可能切断 UTF-8（`machine.go:491-493`），且不处理 Unicode bidi/零宽字符。低风险，建议统一 sanitize。
- **Q-8（GET body / HEAD）**：4 KiB 只对 POST 生效（`machine.go:217`）；`HEAD /api/machine/status` 返回 405。端点用 query，风险低；若验收严格要求所有请求体限长或 HEAD 探活，需要补。
- **Q-9（count 与 maxSlots）**：机器接口允许 count 1..50（`machine.go:30,312-317`），但 `Manager.maxSlots` 默认 20（`main.go:23`）；超出的隧道会在 `runProvision` 里异步失败，而 HTTP 仍返回 200 + job。建议在 machine 层暴露/校验容量，或文档说明「可能部分成功，进度看 job/status」。
- **Q-10（socks_url 用公网 IP）**：status 的 `socks_url` 用 `hostPublicIP()`（`machine.go:267-293`），若管理器与 fanout 同机，公网 IP 可能不 hairpin；响应同时给了 `port`，管理器可自行拼 `127.0.0.1`。建议在 README 说明同机管理器优先用 `127.0.0.1:port`；若 `FANOUT_PUBLIC_IP` 被填成 IPv6，`socksURL` 也不会加方括号（`cred.go:83-88`），属既有格式问题。
- **Q-11（XCL template 判断 TOCTOU）**：见必查项 6；建议把「XCL 忽略 template」下沉到 `Manager.Provision`/`runProvision`，避免 `BackendName()` 瞬时失败导致模板进入 XCL 分支。
- **Q-12（README 与实现的小差异）**：README status 示例 `"country": "日本"`，实际 `nodeLabel` 返回「国旗 + 中文」（`country.go:84-107`），测试用 `Contains` 断言；属文档示例精度问题。`README` 说「start 没有 slot 参数」，但实现只在 host 为空时拒绝 slot（P2-3）。
- **N-1（测试缺口）**：见必查项 8，重点：X-Forwarded-For 负例、base path 为空的默认关闭接线、非 loopback 限流/审计、并发限流、host+slot、stop 竞态、真实 `BackendName`/XCL、真实 `hostPublicIP`、真实 `MaxBytesReader`。
- **N-2（平台与实跑）**：Windows 原生 `go test` 因 `unix.Flock` / `unix.Setns` 无法构建；Linux 测试二进制只编译未执行（无 Linux 运行环境，且按红线不启动二进制）。因此 13 个用例的运行时结果未在本机确认；建议在 Linux/Termux 或 CI 跑 `GOOS=linux go test ./...` 后再给最终合并结论。
- **N-3（harness 局限）**：`fakeMachine` 只模拟 Manager 的 8 个方法；真实 `Manager.Provision` 的异步 job、`Manager.Start` 的隧道创建、`openPanel()` 的后端探测都未覆盖。handler 层测试可靠，但需要 Manager/集成层测试补齐。
- **N-4（token 计数）**：`machineMinTokenLen` 按 rune 计数（`machine.go:131`），16 个非 ASCII 字符也通过；与 README「16 个字符」一致。若要求字节/熵下限，需另行明确。
- **N-5（zoned IPv6）**：`net.ParseIP` 不接受 `[::1%lo0]` 形式的 zone，`isLoopbackRequest` 会判 false（fail closed）；不构成绕过，但若未来监听 zoned 地址需注意。

## 五、实跑输出

在 `D:\LLM\self_programming\fanout`（只读命令，除临时测试二进制外未写工作区）：

```text
$ git rev-parse HEAD
29aa7af8f9069102de86465ab0228b77cfa75754

$ git status --short
# 无输出（工作树干净）

$ git show --stat 29aa7af
 4 files changed, 1089 insertions(+)
 README.md       |  63 +++++++
 machine.go      | 495 +++++++++++++++++++++++++++++++++++++++++++++++++++++
 machine_test.go | 520 ++++++++++++++++++++++++++++++++++++++++++++++++++++++++
 main.go         |  11 ++

$ go version
go version go1.26.4 windows/amd64
```

Linux 交叉构建 / vet：

```text
$ $env:GOOS='linux'; $env:GOARCH='amd64'; $env:CGO_ENABLED='0'; go build ./...
BUILD_EXIT=0

$ $env:GOOS='linux'; $env:GOARCH='amd64'; $env:CGO_ENABLED='0'; go vet ./...
VET_EXIT=0

$ $env:GOOS='linux'; $env:GOARCH='amd64'; $env:CGO_ENABLED='0'; go vet -tests ./...
VET_TESTS_EXIT=0
```

测试编译（Linux 目标，仅编译不执行；临时二进制已删除）：

```text
$ $env:GOOS='linux'; $env:GOARCH='amd64'; $env:CGO_ENABLED='0'; go test -c -o "$env:TEMP\fanout-machine-test.bin" .
TEST_COMPILE_EXIT=0
# 生成文件约 12,207,838 字节，已删除

$ gofmt -l machine.go machine_test.go main.go
# 无输出，GOFMT_EXIT=0
```

Windows 原生测试（仅用于记录平台限制，非业务回归）：

```text
$ go test -count=1 ./...
# fanout [fanout.test]
.\instance.go:156:17: undefined: unix.Flock
.\instance.go:156:41: undefined: unix.LOCK_EX
.\instance.go:156:54: undefined: unix.LOCK_NB
.\instance.go:162:12: undefined: unix.Flock
.\instance.go:162:36: undefined: unix.LOCK_UN
.\netns.go:38:19: undefined: unix.Setns
.\netns.go:38:48: undefined: unix.CLONE_NEWNET
.\netns.go:47:19: undefined: unix.Setns
.\netns.go:47:48: undefined: unix.CLONE_NEWNET
.\netnsguard.go:53:18: undefined: unix.Setns
.\netnsguard.go:53:18: too many errors
FAIL    fanout [build failed]
TEST_EXIT=1
```

说明：

- `GOOS=linux` 的 `go build` / `go vet` / `go vet -tests` / `go test -c` 全部成功，说明本 commit 在 Linux 目标下可编译、13 个测试可编译。
- 未执行 Linux 测试二进制（无 Linux 运行环境，且按红线不启动服务/二进制）；因此未声称 13 个用例运行时全绿。
- Windows 原生 `go test` 的失败是仓库全局 Linux-only syscall 依赖导致，不是本 commit 引入的业务回归。

## 六、只读声明

本轮为 100% 只读独立审查：

- 未修改 fanout 仓库的任何源码、测试、配置、README 或静态产物；fanout 工作树保持干净，`HEAD=29aa7af`。
- 未修改 aistudio-api 仓库的源码、测试、配置或其它文档；aistudio-api 工作区既有未提交改动与本审查无关，未触碰。
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；未 push。
- 未启动真实服务（`main.py` / fanout server）、真实浏览器、真实隧道或任何长期进程；仅执行 Go 编译/静态检查，以及一次性的测试二进制编译（已删除）。
- 未访问 VPS、外网资源、真实 Google 账号或真实凭据；仅使用本地 Go 工具链与已有 module cache。
- 唯一写入：`docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.12-fanout-machine-api.md`。

审查完成时间：2026-11-23。

---

# 二轮复审（279fd9c，2026-11-23 07:48 UTC）

- 复审角色：独立只读 Reviewer
- 复审对象：fanout commit `279fd9cbb552005cd41df67759497ce194ccd807`
  - `fix(machine): 默认关闭 404、启动前置校验与按来源限流`
  - 变更：`README.md`（+17/-?）、`machine.go`（+241）、`machine_test.go`（+294）、`main.go`（+39）、`publicip.go`（+7）、`readonly.go`（+5），共 6 文件 547 insertions / 56 deletions
- 复审方式：逐行核对 `279fd9c` diff 与当前源码；审阅 `machine_test.go` 的 20 个用例；Linux 交叉 build/vet/gofmt；Linux 目标测试编译；未启动服务/浏览器/真实隧道，未执行 Linux 测试二进制
- 复审结论：**PASS（0 P0/P1/P2，可联调）**
- 重要边界：20 个 machine 用例全部在 Linux 目标下编译通过；本机没有 Linux runtime（WSL 无发行版、无 Docker），因此**未执行测试二进制**，不声称 20/20 运行时通过。建议联调前在 Linux 上执行一次 `GOOS=linux GOARCH=amd64 go test ./...`。

## 7. 复核点逐条结论

### 7.1 P1-1：默认关闭 404 与 basepath 前缀 — 闭环

- 位置：`main.go:179-190`、`main.go:204-213`、`machine.go:213-231`、`machine.go:233-250`
- 事实：
  - `main` 现在无条件调用 `buildHandler(mux, auth, mgr, machineTok)`（`main.go:182`）；`buildHandler` 返回 `machineHandler(StripBasePath(auth.Wrap(mux)), mgr, machineToken)`（`main.go:210-213`）。机器接口位于 `auth.Wrap` 之外。
  - `machineHandler` 在 token 为空时返回 `machineDisabledHandler`（`machine.go:213-219`），后者用 `machineAction` 识别机器路径并返回 `http.NotFound`（`machine.go:221-231`），不再返回「机器接口未启用」JSON。
  - `machineAction` 同时识别固定 `/api/machine` 与当前 basepath 前缀 `/<base>/api/machine`（`machine.go:233-250`），因此空 basepath 和已知 basepath 两种配置都不会先落到管理面 401。
- 组装级测试：`TestBuildHandlerMachineDisabledAssembly`（`machine_test.go:739-800`）
  - basepath 为空：`/api/machine/status` 404，`/api/tunnels` 仍 401；
  - basepath=`/machinetest`：`/api/machine/status` 与 `/machinetest/api/machine/status` 均 404，管理面 `/machinetest/api/tunnels` 仍 401；
  - 启用 token 后：固定路径与 basepath 前缀均 200，无 token 返回机器接口 401 + `WWW-Authenticate: Bearer realm="fanout-machine"`。
- 结论：**PASS**。原 P1-1 闭环；测试真实覆盖了生产 `buildHandler` 组装，而不是只测 wrapper。

### 7.2 P2-1：token 校验早于所有重活 — 闭环

- 位置：`main.go:40-45`
- 事实：`machineTokenFromEnv(os.Getenv)` 在 `os.Geteuid`（`:47`）、`MkdirAll`（`:50`）、`initMainNetns`（`:56`）、`lockWorkDir`（`:61`）、`initInstance`（`:69`）、`NewManager`（`:89`）、`restoreState`（`:97`）、`WatchHealth`（`:106`）之前执行；token 太短直接 `log.Fatalf`。
- 结论：**PASS**。误配置不会再先恢复隧道/启动健康检查再退出。
- 测试缺口（N-1）：`main` 的「校验在重活之前」没有组装级单测；本轮以源码顺序核对。可在后续把 `main` 前半段抽成可测的 `preflight`。

### 7.3 P2-2：非 loopback 404、不写审计、不进限流；按来源分桶 — 闭环

- 位置：`machine.go:274-279`、`machine.go:281-295`、`machine.go:152-194`
- 事实：
  - 非 loopback 在 `rl.allow` 与 `machineAudit` 之前直接 `http.NotFound`（`machine.go:274-279`），不写日志、不占桶。
  - 限流键改为 `clientIP(r)`（`machine.go:283`；`auth.go:211-217` 返回不带端口的来源 IP）；`machineRateLimiters` 按 key 各持一个固定窗口桶（`machine.go:152-194`），`127.0.0.1`、`::1`、`127.0.0.2` 各自独立。
  - `sweepLocked` 在桶数量 ≥64 且创建新桶时回收超过两个窗口未动的来源（`machine.go:183-194`）。
- 测试：`TestMachineRateLimitPerSource`（`machine_test.go:540-562`）真实断言 127.0.0.1 耗尽后 429、::1 与 127.0.0.2 仍 200；`TestMachineNonLoopbackNotAuditedOrLimited`（`:564-594`）连续 35 次非 loopback 全部 404、日志缓冲为空、随后 loopback 200。
- 结论：**PASS**。
- 备注（Q/N）：按来源分桶意味着本机可用多个 127.x 源地址获得多个桶；对单管理器场景无影响，但若把限流当本机防爆破，仍需全局上限或按 token 总量限。

### 7.4 P2-3：start 出现 slot 即 400；README 一致 — 闭环（附边界 Q/N）

- 位置：`machine.go:561-573`、`README.md:208-212`、`machine_test.go:446-489`
- 事实：`machineStart` 先检查 `strings.TrimSpace(q.Get("slot")) != ""`，非空即 400，然后才检查 host；测试覆盖 `start?host=node-1&slot=3` 返回 400 且不调用 `Start`；README 已改为「只要请求里出现 slot（无论是否同时给 host）一律返回 400」。
- 结论：**PASS**（原 P2-3 的 `host+slot` 场景闭环）。
- Q/N-1（非阻塞）：`?slot` / `?slot=` 这种「键存在但值为空」时，`q.Get("slot")` 返回空串，代码会继续按 host 启动；严格按 README「出现 slot」应改用 `q.Has("slot")`。当前测试未覆盖空值。

### 7.5 P2-4：并发 stop 返回 200/404，不再 500 — 闭环

- 位置：`machine.go:535-554`、`machine_test.go:665-736`
- 事实：`machineStop` 在 `m.Stop` 返回错误后重查 `machineHasSlot`，不存在则 404，否则 500（`machine.go:545-552`）；`TestMachineStopRaceReturns404` 用 `stopRaceMachine` 让两个 stop 都通过预检后再放行，断言恰好一个 200、一个 404。
- 结论：**PASS**。

### 7.6 Q-5/Q-6：空 token 构造器拒绝、tokenMatches 空 want 拒绝、默认关闭不泄露 — 闭环

- 位置：`machine.go:252-256`、`readonly.go:78-84`、`machine.go:221-231`
- 事实：
  - `newMachineHandler` 在 `token == ""` 时 panic（`machine.go:252-256`）；`TestNewMachineHandlerRejectsEmptyToken`（`machine_test.go:647-654`）用 recover 断言。
  - `tokenMatches` 在 `want == ""` 时直接 false（`readonly.go:78-84`）；`TestTokenMatchesEmptyWantNeverAllows`（`:656-663`）断言空 got/任意 got 都不放行。
  - 空 token 的 `machineDisabledHandler` 返回普通 `http.NotFound`（`machine.go:221-231`）；`TestMachineEmptyTokenNeverAuthenticates`（`:204-222`）断言 `/api/machine/status`、`/api/machine`、`/api/machine/` 均 404，且 body 不含「机器接口」「未登录」。
- 结论：**PASS**。默认关闭不再泄露功能文案。

### 7.7 Q-2/Q-3：public IP 缓存/负缓存、jobs/nodes 白名单 — 闭环（附测试缺口）

- 位置：`machine.go:370-399`、`machine.go:336-345`、`machine.go:443-477`、`README.md:166,173-174`
- 事实：
  - `machineStatusHost` 先看显式 `-ip` / `FANOUT_PUBLIC_IP`（`publicIPOverrideValue`，`publicip.go:34-39`），再走独立缓存：成功 60s、失败负缓存 10s（`machine.go:370-399`）；持锁探测合并并发请求。README 已补说明。
  - `machineRoutes` 新增 `GET /api/machine/jobs` 与 `GET /api/machine/nodes`（`machine.go:336-345`）。
  - `machineNodes` 用显式 `machineNode` 白名单（`machine.go:96-107,443-463`），只挑 hostname/IP/country/country_code/ping/speed_mbps/sessions/residential，不含 `Node.Config`；`TestMachineNodesAndJobs`（`machine_test.go:596-622`）断言 `secret-config-line` / `remote 1.2.3.4` 不出现。
  - `machineJobs` 返回 `JobView`（`machine.go:465-477`）；`JobView` 本身没有 `Config`/`.ovpn` 字段，当前 producer（`provision.go:71-118`）只写 host/exit IP/错误/计数。
- 结论：**PASS**（nodes 为严格白名单；jobs 当前无 `.ovpn`/凭据字段）。
- Q/N-2（非阻塞，测试/设计）：`machineJobs` 直接复用 `JobView`，`JobView.Steps[].Detail` 是自由文本；没有 `machineNode` 那样的严格白名单，也没有测试断言 detail 不含 `.ovpn`/凭据。建议定义 machineJob 专用视图或对 detail 脱敏，并补测试。
- Q/N-3（非阻塞）：`machineStatusHost` 的 60s/10s TTL 没有单测；首个 status 仍可能在锁内串行探测 3×5s，且 `http.Server` 没有读写超时（`webserver.go:52`）。负缓存已缓解轮询放大，但首次探测仍会阻塞。

### 7.8 回归：readonly / 原有路由 / health / mark / loopback+Bearer — PASS

- **readonly 凭据边界不变**：`readonly.go` 只改了 `tokenMatches` 的空 want 保护（`readonly.go:78-84`）；`readonlyTunnel` 字段与 `readonlyTunnels` 未动，仍不含 SOCKS 凭据/Config；`readonly_test.go` 未修改且测试二进制编译通过。
- **原有路由/health/mark 未被破坏**：本 commit 未修改 `health.go`、`tunnel.go`、`manager.go`、`readonly.go` 的既有业务逻辑（readonly 仅 tokenMatches 空 want 保护）；`f93a39a` 的 mark/readonly 能力保留。
- **机器接口仍只允许 loopback + Bearer**：`machine.go:274-302` 仍是「非 loopback 404 → 按来源限流 → Bearer 常量时间比较」；`TestMachineHandlerAuthAndLoopback`（`machine_test.go:178-202`）覆盖 401/404/200。
- **handler 顺序**：机器接口在 `auth.Wrap` 之外（`main.go:210-213`）；非机器路径原样 `next.ServeHTTP`，只读接口由调用方再包一层（`main.go:187-189`），路径不重叠。
- **编译回归**：Linux 交叉 build/vet 全绿（见第 11 节）。

### 7.9 实跑：build/vet/gofmt PASS；20 用例编译 PASS；运行时未执行

- Linux 交叉 build/vet/gofmt：全绿。
- 20 个 machine 用例：Linux 目标 `go test -c` 编译通过；运行时未执行（无 Linux runtime；WSL 无发行版、无 Docker；按红线未启动 Linux 测试二进制）。
- 未执行的 Linux 行为：真实 `http.Server` 的 `RemoteAddr`/TCP loopback、真实 `openPanel()`/XCL 探测、真实 `hostPublicIP()` 外网探测与缓存、真实 `Manager` 的异步 Provision/Start/Swap/Stop/netns/openvpn、`-race` 并发、真实 `MaxBytesReader`/chunked body。

## 8. 残留 P0/P1/P2

**0 个。**

原 P1-1、P2-1、P2-2、P2-3、P2-4 均已闭环；Q-5/Q-6 已闭环；Q-2/Q-3 已补。未发现新的 P0/P1/P2。以下均为不阻塞的 Q/N。

## 9. Q/N（不阻塞）

- **Q-1（部署假设）**：同机反代仍会让 `RemoteAddr` 恒为 `127.0.0.1`，loopback 不是网络边界；机器接口实际只靠 Bearer token。本轮未改。建议 README 明确「反代必须屏蔽 `/api/machine/*`」或改用独立 loopback 监听/Unix socket。
- **Q-2（start 空 slot）**：`?slot` / `?slot=` 未被 400；建议 `q.Has("slot")` 并补空值用例（见 7.4 Q/N-1）。
- **Q-3（jobs 非严格白名单）**：`machineJobs` 复用 `JobView`，`Steps[].Detail` 为自由文本；当前无 `.ovpn`，但无严格白名单/测试锁定（见 7.7 Q/N-2）。
- **Q-4（status 首次探测阻塞）**：`machineStatusHost` 首次探测仍可能在锁内串行 3×5s；`http.Server` 无读写超时；负缓存只缓解后续轮询（见 7.7 Q/N-3）。
- **Q-5（按来源分桶的 127.x 规避）**：本机可用多个 127.x 源地址获得多个桶；`sweepLocked` 仅在 ≥64 且创建新桶时执行。对单管理器无影响；若把限流当本机防爆破，需全局上限或按 token 总量限。
- **Q-6（路径变体）**：`machineAction` 只按字面 path 匹配；`//api/machine/status`、`/%2fapi/machine/status` 等变体可能落到 auth/login（非 404）。不构成 token 绕过（enabled 时仍需 token；disabled 时无机器文案），但「完全 404」对路径变体不严格。
- **Q-7（注释同步）**：`machine.go:23` 的 `machineRateLimit` 注释仍写「每个 token 的调用频率上限」，实现已改为按来源；建议同步为「每个来源」。
- **Q-8（nodes 与 residential_only）**：`machineNodes` 返回全量节点（含机房），只带 `residential_only` 标志；`machineStart` 仍用 `m.Nodes()` 原表，不经过 `nodePoolLocked()`，开启「只用家宽」时仍可按 host 启动机房节点（第一轮 Q-4 的延续）。
- **Q-9（测试隔离）**：`TestBuildHandlerMachineDisabledAssembly` 的 cleanup 只 `setBasePath("")`，把 `basePathDir` 留在即将删除的 temp dir；后续若有无 `initBasePath` 的 `setBasePath` 会失败。建议保存/恢复原 `basePathCur`/`basePathDir`。
- **Q-10（XCL template TOCTOU）**：`machineProvision` 仍在 handler 里用 `m.BackendName()` 判断 XCL（`machine.go:503-506`）；`openPanel()` 瞬时失败时 `tpl` 不会清零，随后 `runProvision` 对 XCL 调 `CloneToTunnels` 得到 `errXCLReadOnly`（`panel_xcl.go:382-384`）。建议下沉到 `Manager.Provision`/`runProvision`。
- **Q-11（README 审计文案）**：README 说审计只记 slot/region，但 jobs/nodes 实际记 `jobs=N` / `nodes=N` 计数；可把文案放宽为「slot/region/计数」。
- **N-1（运行时未执行）**：20 个用例只完成 Linux 目标编译，未执行；`main` 的「token 校验早于重活」没有组装级单测。
- **N-2（测试缺口）**：`?slot=` 空值、jobs detail 脱敏、status cache TTL/负缓存、路径变体、限流 sweep/64 桶/并发、真实 `openPanel`/XCL、真实 `hostPublicIP`、真实 `MaxBytesReader` 均未覆盖。
- **N-3（harness 局限）**：`fakeMachine` 只模拟 Manager 的 8 个方法；真实 `Manager.Provision` 异步 job、`Manager.Start`/`Stop`/`Swap` 的隧道行为、`openPanel()` 后端探测未覆盖。handler 层测试可靠，但 Manager/集成层仍需 Linux 实测。

## 10. 20 个 machine 用例清单与结果

> 结果列说明：**编译 PASS** = Linux 目标 `go test -c` 编译通过且源码断言成立；**运行时 N/E** = 未执行（无 Linux runtime，按红线未启动测试二进制）。因此不能写「20/20 运行时 passed」。

| # | 用例 | 关键断言 | 编译/源码 | 运行时 |
|---|---|---|---|---|
| 1 | `TestMachineTokenFromEnv` | 空/空白/15/16/多字节/trim | PASS | N/E |
| 2 | `TestMachineRateLimiter` | 固定窗口 2 次放行、第 3 次 429、窗口重置 | PASS | N/E |
| 3 | `TestMachineHandlerAuthAndLoopback` | 无/错 token 401、非 loopback 404、`[::1]` 200 | PASS | N/E |
| 4 | `TestMachineEmptyTokenNeverAuthenticates` | 空 token 下固定/根路径 404、无功能文案、非机器路径透传 | PASS | N/E |
| 5 | `TestMachineHandlerMethods` | GET provision 405 + Allow、POST status 405 + Allow、未知 404 | PASS | N/E |
| 6 | `TestMachineStatusShape` | status 白名单/socks_url/凭据/敏感串/Cache-Control | PASS | N/E |
| 7 | `TestMachineRateLimitHTTP` | 30 次 200、第 31 次 429 + Retry-After | PASS | N/E |
| 8 | `TestMachineProvisionValidation` | 9 个非法参数 400 且不调 Provision、合法参数透传 | PASS | N/E |
| 9 | `TestMachineProvisionIgnoresTemplateInXCL` | XCL 下 TemplateID=0 | PASS | N/E |
| 10 | `TestMachineSwapAndStop` | 400/404/200/500 映射与调用记录 | PASS | N/E |
| 11 | `TestMachineStart` | 缺 host/仅 slot/host+slot 400、未知 host 404、成功 slot/port | PASS | N/E |
| 12 | `TestMachineBodyLimit` | 4097 字节 400 且不进入业务 | PASS | N/E |
| 13 | `TestMachineAuditLogHasNoToken` | 审计有动作/结果，无 token/Authorization | PASS | N/E |
| 14 | `TestMachineRateLimitPerSource` | 127.0.0.1 耗尽 429；::1 / 127.0.0.2 独立桶 200 | PASS | N/E |
| 15 | `TestMachineNonLoopbackNotAuditedOrLimited` | 非 loopback 35 次 404、无审计、不占本机桶 | PASS | N/E |
| 16 | `TestMachineNodesAndJobs` | nodes 不含 Config；jobs ID/过滤 | PASS（jobs detail 未断言） | N/E |
| 17 | `TestNewMachineHandlerRejectsEmptyToken` | 空 token 构造器 panic | PASS | N/E |
| 18 | `TestTokenMatchesEmptyWantNeverAllows` | 空 want 永不放行 | PASS | N/E |
| 19 | `TestMachineStopRaceReturns404` | 并发 stop 恰好一个 200、一个 404 | PASS | N/E |
| 20 | `TestBuildHandlerMachineDisabledAssembly` | 真实 mux+auth+basepath：空/非空 basepath 404、管理 401、启用 200/401 | PASS | N/E |

合计：**20 个用例，Linux 编译通过；运行时未执行。**

## 11. 实跑输出

在 `D:\LLM\self_programming\fanout`（只读命令，临时测试二进制已删除）：

```text
$ git log --oneline -2
279fd9c fix(machine): 默认关闭 404、启动前置校验与按来源限流
29aa7af feat(machine): loopback token 机器接口（补出口/换节点/读状态）

$ git status --short
# 无输出（工作树干净）

$ git show --stat 279fd9c
 6 files changed, 547 insertions(+), 56 deletions(-)
 README.md       |  17 +++-
 machine.go      | 241 ++++++++++++++++++++++++++++++++++++++++++++++++++++++------
 machine_test.go | 294 +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++-
 main.go         |  39 +++++---
 publicip.go     |   7 ++
 readonly.go     |   5 +-

$ go version
go version go1.26.4 windows/amd64

$ $env:GOOS='linux'; $env:GOARCH='amd64'; $env:CGO_ENABLED='0'; go build ./...
BUILD_EXIT=0

$ $env:GOOS='linux'; $env:GOARCH='amd64'; $env:CGO_ENABLED='0'; go vet ./...
VET_EXIT=0

$ $env:GOOS='linux'; $env:GOARCH='amd64'; $env:CGO_ENABLED='0'; go vet -tests ./...
VET_TESTS_EXIT=0

$ gofmt -l main.go machine.go machine_test.go publicip.go readonly.go
# 无输出，GOFMT_EXIT=0

$ $env:GOOS='linux'; $env:GOARCH='amd64'; $env:CGO_ENABLED='0'; go test -c -o "$env:TEMP\fanout-279fd9c-test.bin" .
TEST_COMPILE_EXIT=0
# 生成文件 12,342,979 字节，已删除
```

Windows 原生测试（平台限制，非业务回归）：

```text
$ go test -count=1 ./...
# fanout [fanout.test]
.\instance.go:156:17: undefined: unix.Flock
.\instance.go:156:41: undefined: unix.LOCK_EX
.\instance.go:156:54: undefined: unix.LOCK_NB
.\instance.go:162:12: undefined: unix.Flock
.\instance.go:162:36: undefined: unix.LOCK_UN
.\netns.go:38:19: undefined: unix.Setns
.\netns.go:38:48: undefined: unix.CLONE_NEWNET
.\netns.go:47:19: undefined: unix.Setns
.\netns.go:47:48: undefined: unix.CLONE_NEWNET
.\netnsguard.go:53:18: undefined: unix.Setns
.\netnsguard.go:53:18: too many errors
FAIL    fanout [build failed]
WINDOWS_TEST_EXIT=1
```

未执行 Linux 测试二进制：

- `wsl.exe -l -v` 显示没有已安装的 WSL 发行版；`docker.exe` 不存在。
- 按只读红线未启动 Linux 测试二进制/服务/浏览器/真实隧道。
- 因此第 10 节的「运行时」列均为 N/E；建议在 Linux/Termux 或 CI 执行 `GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go test ./...` 后再作为联调放行证据。

## 12. 只读声明（二轮）

- 未修改 fanout 仓库任何源码、测试、配置、README 或产物；fanout 工作树保持干净，`HEAD=279fd9c`。
- 未修改 aistudio-api 仓库的源码、测试、配置或其它文档；aistudio-api 工作区既有未提交改动与本审查无关，未触碰。
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；未 push。
- 未启动真实服务（`main.py` / fanout server）、真实浏览器、真实隧道或长期进程；仅执行 Go 编译、静态检查与一次性测试二进制编译（已删除）。
- 未访问 VPS、外网资源、真实 Google 账号或真实凭据；仅使用本地 Go 工具链与已有 module cache。
- 唯一写入：本文件的「二轮复审」追加段落。

二轮复审完成时间：2026-11-23。
