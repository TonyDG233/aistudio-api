# REV-2.1.5.8 — app 出口池 / 宿主出口管理器 / fanout 只读补丁 独立只读审查

- 审查日期：2026-11-23
- 审查范围：
  1. aistudio-api 工作区未提交改动：`src/aistudio_api/infrastructure/exit_pool/`、`application/exit_arbitration.py`、`engine_runtime.py`、`domain/errors.py`、`application/routing_pipeline.py`、`api/app.py`、`infrastructure/gateway/instance_pool.py`、`tests/unit/test_exit_pool.py`、`tests/unit/test_exit_arbitration.py`。
  2. 宿主出口管理器：`data/tools/exit-manager/`（Python 标准库 + WARP 多实例脚本 + 95 个 unittest + DEPLOY.md）。
  3. fanout fork 未提交补丁：本地独立 fork 工作区（`health.go` / `main.go` / `tunnel.go` / 新增 `readonly.go` + `health_test.go` / `readonly_test.go`）。
- 审查方式：100% 只读；未启动真实服务/浏览器；未访问 VPS/外网/账号凭据；未执行 `git add/commit`。
- 设计依据：`docs/dev/rebuild/domains/P1-account/research/exit-pool/EVALUATION.md` 与 `AGENT.md` 既有红线。

---

## 一、结论

**CONDITIONAL（有条件通过）**

- 0 × P0。
- **1 × P1（P1-1）**：app 侧 canary 读取凭据时把真实 `AccountStore.get_auth_path_optional()` 返回的 `pathlib.Path` 当成非字符串丢弃，导致 canary 永远返回 `inconclusive`，`exit_bad` / `account_bad` 两条仲裁分支在真实部署中不可达。功能不产生安全回归，但本轮交付的核心“鉴权级 canary 仲裁”实际失效，须修复后复验。
- 11 × P2（防御性/一致/运维项，均可定位到 file:line；其中 P2-3 / P2-4 涉及 EVALUATION.md 明文设计口径，建议随 P1 一并处理或明确延后）。
- 7 × Q、10 × N。

除 P1-1 外，默认 env 缺失时确实无 resolver、无 HTTP、无 `settings.proxy_url` 改写、导入期无网络副作用；D-05 顺序、`active_requests` 唯一并发账本、fanout 默认 `mark` 不重连、管理器鉴权/CIDR/原子写/迁移/恢复均实测或静态核对成立。P1 修复后本批可转 PASS。

---

## 二、按必查项 A–J 的逐条结论

| 项 | 结论 | 关键证据 |
|---|---|---|
| A. 默认无感 | **PASS**（附 P2-6、N-4） | `exit_pool/config.py:76-92` env 缺失/未知值直接 `None`；`resolver.py:398-411` 只读 env，`httpx` 客户端延迟到 `_ensure_client()`；`engine_runtime.py:156-160` 无 resolver 时构造 `None`；`engine_runtime.py:312-315` 只记录 surface；`routing_pipeline.py:478-482` off 时 `exit_arbitration=None`；`test_exit_pool.py:73-75,388-436` 证明 off 不建 resolver / 不建 httpx client / 用全局代理。 |
| B. 契约一致性 | **PASS**（P2-1/P2-5/Q-7 附注） | `resolver.py:225-285` 与 `server.py:148-198` 逐字段对齐：`GET /v1/assign?account=<hmac>&kind=chat|build`、Bearer、`exit_id/socks_url/generation/ttl_s/healthy`、503 `no_healthy_exit`、202 events；HMAC 只发摘要（`resolver.py:41-47,308-322`）；socks scheme/host/port 白名单在 `resolver.py:50-69`，字面 host 匹配，恶意 host 被拒（`test_exit_pool.py:137-156`）。 |
| C. 403 分类与仲裁 | **PASS**（Q-6 附注） | `errors.py:21-40,297-320` 只由 403/grpc7 分支置 `region_restricted`，且 verify 优先；`routing_pipeline.py:953-981` 顺序 verify → region → 其他；`routing_pipeline.py:977` 用 `not has_yielded_data` 保证已出正文不仲裁；canary 走 `BrowserlessSession.send_hooked_request`（`exit_arbitration.py:117-126`），非裸 httpx；9s 超时（`:38`）、全局并发 1（`:172,208-214`）、每出口 600s 冷却（`:39,221`）均生效并有测试（`test_exit_arbitration.py:392-426`）；`account_bad` 不失效出口（`:256-263`），无账号冷却（`account_orchestrator.py:65-71` 只累加 `auth_errors`）。 |
| D. D-05 与并发账本 | **PASS**（P2-11 附注） | `routing_pipeline.py:1246-1308` 顺序为「`release` → `active_requests==0` → `restart`（内部 `_close_runtime` 后重建）→ 一次重试」；`instance_pool.py:604-655` 在 `lifecycle_lock` 下检查 `active_requests>0` 并先关旧 runtime；忙/失败路径 `raise exc` 安全降级（`routing_pipeline.py:1259-1289`）；`ActiveRequest.release()` 幂等（`instance_pool.py:122-158`），`active_requests` 仍是唯一请求账本；`instance_pool.py:682-690` 是唯一 `engine_runtime.create_session` 调用方，`surface` 默认值不破坏其它调用方。 |
| E. canary 安全 | **PASS**（P2-4/Q-1/Q-2 附注） | 真实 auth.json 复制到 `tempfile.mkdtemp` 临时目录后使用（`exit_arbitration.py:104-116`），`finally` 关会话 + `rmtree`（`:133-145`）；临时目录父级无 `registry.json`，`sync_sibling_storage_state_files` / `_mark_root_session_expired` 均提前返回（`account_store.py:223-226`、`browserless_session.py:436-440`）；canary 不取 `active_requests`、不建实例（`engine_runtime.py:317-348`）；测试证明真实文件字节不变（`test_exit_arbitration.py:508-545`）。 |
| F. 管理器正确性与安全 | **PASS**（P2-2/P2-3/P2-5/P2-9/Q-5 附注） | 粘性/容量/迁移：`manager.py:292-325,222-242,329-403`；恢复：`:405-481`；事件幂等：`:568-625` + 重启持久化（`test_manager_events.py:70-81`）；重启保持：`test_manager_assign.py:117-122`；token 非空：`config.py:368-370`；loopback/CIDR：`config.py:18,318-352`、`server.py:21-29,56-59,120-146`；仅 GET：`server.py:148-176`；原子写/损坏：`store.py:17-67`、`test_store.py`；L1 阈值：`health.py:171-209` + `manager.py:485-525`；不读账号文件：管理器包无 Cookie/registry 读取（grep 仅注释）；日志脱敏：`util.py:48-62`、`server.py:32-33,48-54`；单写者假设：`DEPLOY.md:318,378-379`。 |
| G. fanout 补丁 | **PASS**（Q-7、N-2 平台附注） | 默认 `mark` 分支永不调用 `reconnect`（`health.go:87-137`），`parseIPGuardMode` 默认 `mark`（`:35-50`）；`suspect` 写入/读取受 `Tunnel.mu` 保护（`tunnel.go:329-357`）；只读接口仅 loopback + Bearer + GET（`readonly.go:129-188`），载荷无 `cred`/`socks`/`config`/`ovpn`（`:18-27,44-60`，`readonly_test.go:52-63`）；非只读路径原样交回内层 handler（`readonly_test.go:96-101`）；linux 交叉构建 + vet 通过（见 §四）。 |
| H. 测试质量 | **PASS**（P2-10 附注） | 新增 45 个 app 用例、管理器 95 个用例；off 模式零网络/零 client 有断言（`test_exit_pool.py:388-436`）；仲裁三态 + yield + countTokens + cooldown + 并发 1 + 事件失败降级均覆盖（`test_exit_arbitration.py:256-445`）；管理器覆盖迁移/503/幂等/重启/健康阈值（`test_manager_assign.py`、`test_server.py:115-124`）。**但 P1-1 被 `_FakeStore` 返回 `str` 掩盖**（`test_exit_arbitration.py:487-495`），且忙/restart 失败/二次 exit_bad 未覆盖。 |
| I. 红线核对 | **PASS**（N-5 附注） | 无 `defaults/config.schema.json` / `web/` / UI 变更（`git diff --name-only` 仅 5 个 src 文件）；无第二套请求并发账本（probe/fetch lock 不参与准入）；app/管理器无定时/流量轮换（WARP 仅隔离/恢复触发，`DEPLOY.md:13,160`）；无后台自动 rebind/rebalance；管理器不接收 Cookie（`DEPLOY.md:9`，包内无读取）；`data/` 被 `.gitignore:12` 忽略且 `Dockerfile` 只 `COPY src/ main.py defaults/...`，默认分发无管理器代码。 |
| J. 实跑门禁 | **PASS**（N-1 基线附注） | 见 §四。ruff check / pyright / pytest 1791 / 管理器 95 / fanout linux build+vet 全绿；`ruff format --check .` 61 个基线文件（与 REV-2.1.5.6 台账一致），本轮 6 个新文件全部已格式化。 |

---

## 三、P0 / P1 / P2 清单

### P0

无。

### P1

#### P1-1 canary 凭据路径被 `isinstance(value, str)` 丢弃，仲裁永远 inconclusive

- **问题**：`SignerCanaryProbe._auth_file()` 只接受 `str`：
  `src/aistudio_api/application/exit_arbitration.py:93`
  ```python
  return value if isinstance(value, str) and value else None
  ```
  但真实 `AccountStore.get_auth_path_optional()` 返回 `Path | None`：
  `src/aistudio_api/infrastructure/account/account_store.py:1173-1184`
  ```python
  def get_auth_path_optional(...) -> Path | None:
      ...
      path = _safe_account_dir(self._accounts_dir, acc.id) / "auth.json"
      ...
      return path
  ```
  `Path` 不是 `str` 子类，故 `_auth_file()` 恒为 `None`，`probe()` 走
  `exit_arbitration.py:101-102` 的 `canary-credentials-missing` → `inconclusive`。
  app 注入的正是真实 `AccountStore`（`src/aistudio_api/api/app.py:352,494-497`），
  因此 `exit_bad`（`exit_arbitration.py:245-255`）与 `account_bad`（`:256-263`）
  在真实部署中不可达；只有 `inconclusive` 会被上报。
- **实测证据**（只读，无网络）：
  ```
  AccountStore.get_auth_path_optional -> {'return': pathlib._local.Path | None}
  Path is str? False
  SignerCanaryProbe._auth_file() with a Path-returning store -> None
  ```
- **为何测试没抓住**：`tests/unit/test_exit_arbitration.py:487-495` 的 `_FakeStore` 返回 `str(self._auth_path)`，与生产返回类型不一致。
- **建议修法**：接受 `Path`：
  ```python
  from pathlib import Path
  ...
  if isinstance(value, Path):
      value = str(value)
  return value if isinstance(value, str) and value else None
  ```
  或直接 `value = getattr(value, "__fspath__", None) and str(value)`；
  并新增“store 返回 `Path`”的回归用例（最好直接对真实 `AccountStore.get_auth_path_optional` 签名/一个 Path-returning stub 断言）。
- **影响面**：仅外部出口池 canary 分支；无安全回归（失败即退回既有 403 逻辑），但本轮核心功能失效。

### P2

#### P2-1 管理器返回 `healthy:false` 时 app 仍使用该出口

- 证据：`src/aistudio_api/infrastructure/exit_pool/resolver.py:275-285` 只 `log.info`，仍构造并返回 `ExitAssignment`；`resolve()`（`:188-213`）直接返回 `assignment.socks_url`；`engine_runtime.py:292-315` 直接注入。
- 当前管理器 `assign()` 只选 `STATUS_HEALTHY`（`data/tools/exit-manager/exit_manager/manager.py:292-325`），所以实测不会触发；但契约显式携带 `healthy`，app 应防御性拒绝 `healthy is False`（返回 `None` 回退全局代理），否则未来管理器/快照异常会把请求打到已知坏出口。

#### P2-2 事件去重摘要先落盘、隔离状态后落盘，崩溃窗口会丢隔离

- 证据：`manager.py:591-606` 先写 `_recent_events[digest]` 并 `_save_exits()`，随后 `:617-625` 才 `quarantine()`；若在两步之间崩溃，重启后同一事件被判 `duplicate`，而出口仍是 healthy。
- 建议：先执行 `quarantine`（幂等）再持久化 digest，或持久化 `pending` 状态；测试可注入“digest 落盘后抛异常”的故障。

#### P2-3 `exit_bad` 对 WARP 出口立即轮换，未感知同出口其它账号在途流

- 证据：`manager.py:619-623` 以默认 `rotate=True` 调 `quarantine`；`:388-396` 在 `rotate_on_isolation` 为真时 `_schedule_rotation`；`:683-695` 后台线程执行 `_run_rotate_command`；`config.py:271-280` 默认 `rotate_on_isolation=True`；`rotate-instance.sh:73` `systemctl restart` 会断开该出口所有连接。
- `EVALUATION.md:222` 明确“有在途请求的出口只停止新分配，等在途结束再处理”。当前管理器没有在途信号，发现 `exit_bad` 即重启 wireproxy，可能截断同出口其它账号已出正文的流（app 的“已出正文不切换”只保护本请求）。
- 建议：默认 `rotate_on_isolation=false`（仅 mark+迁移），或在事件契约中加入在途计数/延迟轮换；若产品要求绝不截断并发流，此项应升为 P1 并补契约。

#### P2-4 canary 失败/污染后未停用 canary 账号

- 证据：`exit_arbitration.py:151-153` 对非 200/非 region 的 403 返回 `inconclusive`；`_apply`（`:230-270`）只上报事件，没有任何 `set_account_enabled(False)`/污染标记/停用动作。
- `EVALUATION.md:148` 明文要求“canary 返回 verify-account 或其他非 region 失败 → inconclusive ... 同时把该 canary 账号视为受污染并停用”。当前只靠 600s 出口冷却（`:221`）限频，未停用账号。
- 建议：对 `verification_required`/非 region 403 记录污染并停用 canary（或至少 loud warning + 长退避），避免反复把 canary 打进 verify。若本轮以 EVALUATION.md 为准，此项应视为 P1。

#### P2-5 配置允许的 exit id 与运行时事件/恢复校验不一致

- 证据：`data/tools/exit-manager/exit_manager/config.py:169` 只要求 `id` 非空字符串；`manager.py:40` 定义 `EXIT_ID_RE = ^[A-Za-z0-9._:-]{1,128}$`，`:208-212` 的 `_validate_exit_id` 用于 `record_event`（`:572`）与 `recover`（`:406`）。
- 后果：配置 `id="warp/1"` 可被 `assign` 返回，但该出口的事件/恢复会被 400 拒绝。
- 建议：`_parse_exit` 用 `EXIT_ID_RE` 校验 id，fail-fast。

#### P2-6 `AISTUDIO_EXIT_ENDPOINT` 未校验 scheme，与“malformed value keeps feature off”不符

- 证据：`config.py:94` 仅 `rstrip("/")`；`config.py:1-6` 文档声称 malformed 值会关闭功能；实际非法 endpoint 会在每次 `resolve()` 时由 `resolver.py:231-241` 捕获并回退，日志噪音且功能未“关闭”。
- 建议：加载时要求 `http://`/`https://` 且可 `urlsplit`，否则返回 `None`。

#### P2-7 `ExitResolver.aclose()` 无人调用，shutdown 不释放控制端点连接池

- 证据：`resolver.py:384-392` 定义 `aclose()`；`src/aistudio_api/api/app.py:233-264` 的 `_shutdown_runtime` 只关闭 keepalive/instance_manager/engine_runtime/client，未关闭/重置 exit resolver。
- 建议：在 `_shutdown_runtime` 中 `await get_exit_resolver()?.aclose()` 后 `reset_exit_resolver()`；注意避免在 UI debug/测试多 lifespan 场景复用旧 loop 的 client。

#### P2-8 隔离出口只失效上报账号的缓存，其它账号在 TTL 内继续用坏出口

- 证据：`exit_arbitration.py:254` 仅 `invalidate(account_id, kind=kind)`；`resolver.py:175-186` 只清单个 `(account, kind)`；管理器 `quarantine` 已迁移该出口的全部绑定（`manager.py:358-379`）。同时首个 `exit_bad` 设置 600s 出口冷却（`:221`），其它账号的 region 403 在冷却期内直接 `inconclusive`，无法触发同出口的二次仲裁。
- 后果：同出口其它账号最多继续使用坏出口 300s（默认 TTL）。
- 建议：resolver 增加 `invalidate_exit(exit_id)`（扫描 `_last_known`），在 `exit_bad` 时失效该出口所有账号；或由管理器事件反向通知。

#### P2-9 `probing` 状态在崩溃后可永久卡死

- 证据：`manager.py:420-421` 在 `recover` 前把状态写为 `PROBING`；`state.py:80-92` 的 `apply_persisted` 会恢复 `PROBING`；但 `manager.py:527-533` 的 `exits_for_health` 只返回 `HEALTHY`，`:232` 的 `_pick_exit` 只选 `HEALTHY`。若 `recover` 期间进程被杀，重启后该出口既不分配也不健康轮询，只能人工再次 `recover`（`:405-412` 允许 probing 进入）。
- 建议：加载时把 `PROBING` 归一为 `HEALTHY` 或 `QUARANTINED`（二选一，建议后者更保守），并补重启用例。

#### P2-10 测试缺口（P1 被 fake 类型掩盖；重建忙/失败/二次命中未覆盖）

- 证据：`tests/unit/test_exit_arbitration.py:487-495` 的 `_FakeStore` 返回 `str`，未覆盖真实 `Path`；`tests/unit/test_exit_arbitration.py` 未覆盖 `_rebuild_for_new_exit` 的 `active_requests>0`、`restart` 抛异常、`exit_rebuild_retried` 二次 `exit_bad` 三条降级路径；`test_exit_pool.py` 未覆盖 `healthy:false`。
- 建议：补 4 个用例：Path store、busy 降级、restart 失败、二次 exit_bad 不再重建。

#### P2-11 region 仲裁先于本地 tier 检查

- 证据：`routing_pipeline.py:973-981` 的 region 分支位于 `:982` 的 `if not tier_allows(...)` 之前。若一次 403 来自不满足 route tier 的候选，会先花一次 canary 探针并可能同槽重建，再抛 `TierRequiredError`。
- 建议：把 region 仲裁放在 `tier_allows` 之后，或在该分支加 `and tier_allows(attempt.candidate.tier, context.route)`。

---

## 四、Q（疑问）与 N（备注）

### Q

- **Q-1** canary 会话固定 `auth_user="0"` / `tier="Free"`（`engine_runtime.py:317-348`），未读取 canary 账号的 `AccountMeta.auth_user`/`tier`。若 canary 是 `u/1` 子号或 Pro 号，探针可能 inconclusive 或语义偏移。
- **Q-2** canary 账号未与实例槽位做互斥校验：`build_exit_arbitration`/`create_canary_session` 不检查该账号是否已绑定实例；而 `BrowserlessSession.close()` 会 `release_worker(account_key=...)`（`browserless_session.py:803-813`）。EVALUATION.md:143 约定 canary 用 `enabled=false` 小号，建议启动时校验并告警。
- **Q-3** canary 用 `ListModels` + `body="[]"`，与真实模型发现一致（`model_discovery.py:1181-1196`）；但新建 canary 会话没有 `_mint` proof 头，若 ListModels 需要 proof，探针可能恒 inconclusive。需确认 signer 对 ListModels 是否免 proof。
- **Q-4** per-account 出口只注入 `BrowserlessEngineRuntime.create_session`（`engine_runtime.py:266-315`）；无活跃槽位时，keepalive/控制面 Cookie 刷新可能仍走全局代理（`account_keepalive.py:470-535`）。是否会拆分账号的出口身份，需要确认 keepalive 在 SLEEPING 槽位上始终使用槽位会话。
- **Q-5** 管理器绑定以 account 为键，chat/build 共用同一出口；同一账号切换 kind 时 `binding.kind` 被改写（`manager.py:298-305`），`capacity_by_kind` 记账随之漂移。“一账号一出口、kind 只影响容量记账”是否是预期语义？
- **Q-6** `region_restricted` 由 `classify_error` 在任何 403 上设置，包括实例客户端的 media/上传路径（`client.py:304,384`、`instance_client.py:171,209`、`streaming.py:241`）；流水线只按 `record_attempts`/`has_yielded_data` 消费。media 上传阶段的 region 403 是否也属于出口级仲裁范围？（从出口视角看应属同一出口，故未判缺陷。）
- **Q-7** fanout 只读载荷没有 `id` 字段（`readonly.go:18-27`），管理器 `tunnel_matches` 实际只能按 socks 端口匹配（`fanout.py:104-119`）；若配置的 `socks_url` 端口与 fanout 隧道 `port` 不一致，`refresh_fanout` 会静默跳过。部署是否保证端口一致？

### N

- **N-1 格式基线**：`uv run ruff format --check .` 仍为 `61 files would be reformatted, 349 files already formatted`（exit 1），与 `REV-2.1.5.6-discovery-fallback-removal-and-signer-tuning.md` 记录的 61 文件基线一致；本轮 6 个新增 Python 文件单独检查为 `6 files already formatted`（exit 0），非本批引入。
- **N-2 门禁**：ruff check 0 / pyright 0 / pytest 1791 passed / 管理器 95 OK / 新增 app 45 passed；fanout `GOOS=linux GOARCH=amd64 go build ./...` 与 `GOOS=linux GOARCH=amd64 go vet ./...` 均 exit 0。Windows 目标 `go vet ./...` 在 `instance.go:156 undefined: unix.Flock` 失败，属既有平台差异（该文件不在本补丁）；未执行 `go test`（遵守“不运行二进制”）。
- **N-3 构建产物**：允许的 `GOOS=linux GOARCH=amd64 go build ./...` 生成了 fanout 仓库内被 `.gitignore:1` 忽略的 `fanout` 二进制（约 11.3 MB）；`git status --short` 确认无 tracked 文件变化。
- **N-4 测试环境隔离**：`tests/conftest.py` 未清理 `AISTUDIO_EXIT_*`；若开发/CI 环境预设 `AISTUDIO_EXIT_MODE=external`，`RoutingPipeline.__init__` 的 fallback（`routing_pipeline.py:478-482`）会在无关用例中构造 resolver。建议加入 hermetic fixture。
- **N-5 分发边界**：管理器位于被 `.gitignore:12` 忽略的 `data/`，`Dockerfile:194-213` 只复制 `src/`、`main.py`、`defaults/config.yaml`，默认镜像不含管理器；本交付需 VPS 侧复制，无法直接随仓库提交（符合红线 I）。
- **N-6 状态文件含 socks_url**：`manager.py:164-175` 把 `socks_url` 写入 `exits_state.json`（若含 userinfo 则落盘）；状态目录 0700、`/v1/status` 不返回 socks_url，风险可接受。
- **N-7 fanout 只读读取的锁范围**：`readonly.go:44-50` 读取 `Status/ExitIP/Node` 未持 `t.mu`，与既有 `/api/tunnels`（`manager.go:52-60`）同模式；`suspect` 本身经 `isSuspect()` 受锁保护，不属本补丁新增竞态类。
- **N-8 持久化 Binding.kind 未校验**：`state.py:141` 接受任意字符串，损坏/历史状态可留下 `kind="audio"` 影响按 kind 容量计数；`assign`/`record_event` 不校验。低风险。
- **N-9 canary 临时目录隔离已实测**：`test_exit_arbitration.py:508-545` 断言 `auth_file != real_auth`、真实文件字节不变、会话关闭；`account_store.py:223-226`/`browserless_session.py:436-440` 的 registry 守卫确保不会同步兄弟/标记失效。
- **N-10 传输面**：app→管理器为 loopback/bridge 明文 HTTP + Bearer（`resolver.py:231-235`），token 不加密；符合文档本地威胁模型，禁止把管理器暴露到 loopback/bridge 之外。

---

## 五、实跑门禁输出

以下命令均在仓库根目录（fanout 除外）执行，未修改 tracked 文件、未启动服务/浏览器、未联网、未使用账号凭据。

### 5.1 工作区与 diff

```
$ git status --short
 M src/aistudio_api/api/app.py
 M src/aistudio_api/application/routing_pipeline.py
 M src/aistudio_api/domain/errors.py
 M src/aistudio_api/infrastructure/gateway/instance_pool.py
 M src/aistudio_api/infrastructure/waa/engine_runtime.py
?? docs/dev/rebuild/domains/P1-account/research/exit-pool/
?? src/aistudio_api/application/exit_arbitration.py
?? src/aistudio_api/infrastructure/exit_pool/
?? tests/unit/test_exit_arbitration.py
?? tests/unit/test_exit_pool.py

$ git diff --check
（无输出，exit 0）
```

fanout：

```
$ git -C <fanout 本地路径> status --short
 M health.go
 M main.go
 M tunnel.go
?? health_test.go
?? readonly.go
?? readonly_test.go
```

### 5.2 Python 门禁

```
$ uv run ruff check .
All checks passed!
EXIT=0

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations
EXIT=0

$ uv run pytest -q
1791 passed in 50.52s
EXIT=0

$ uv run pytest tests/unit/test_exit_pool.py tests/unit/test_exit_arbitration.py -q
45 passed in 0.28s
EXIT=0
```

### 5.3 格式门禁（基线说明）

```
$ uv run ruff format --check .
61 files would be reformatted, 349 files already formatted
EXIT=1

$ uv run ruff format --check src/aistudio_api/infrastructure/exit_pool \
    src/aistudio_api/application/exit_arbitration.py \
    tests/unit/test_exit_pool.py tests/unit/test_exit_arbitration.py
6 files already formatted
EXIT2=0
```

`61 files` 与既有 `REV-2.1.5.6` 台账一致；被点名的示例 `tests/unit/test_ui_debug_logs.py:64,69` 不在本批 diff 内。本轮新增 6 个文件全部已格式化（exit 0）。

### 5.4 管理器 unittest

```
$ python -m unittest discover -s data/tools/exit-manager/tests -v
...
Ran 95 tests in 3.038s

OK
EXIT=0
```

### 5.5 fanout 交叉构建 / vet

```
$ $env:GOOS="linux"; $env:GOARCH="amd64"; go build ./...
BUILD_EXIT=0

$ $env:GOOS="linux"; $env:GOARCH="amd64"; go vet ./...
VET_LINUX_EXIT=0
```

平台备注（非本补丁问题）：

```
$ go vet ./...      # Windows target
vet.exe: .\instance.go:156:17: undefined: unix.Flock
VET_EXIT=1
```

`instance.go` 不在本补丁内；linux target 下 vet 通过。按只读约定未执行 `go test`（会运行测试二进制）；新增 `health_test.go` / `readonly_test.go` 为真实 Go 测试文件而非 overlay shim，已被 `go vet` 类型检查，但本审查未执行其运行时断言。

### 5.6 P1-1 运行时复核（只读）

```
$ uv run python -c "... AccountStore.get_auth_path_optional ..."
(self, account_id: 'str', *, require_exists: 'bool' = False) -> 'Path | None'
{'return': pathlib._local.Path | None}
Path is str? False

$ uv run python -c "... SignerCanaryProbe(... _FakeStore returning Path ...)._auth_file()"
auth_file -> None
```

即真实 `AccountStore` 返回的 `Path` 被 `_auth_file()` 丢弃，canary 无法取到凭据。

---

## 六、只读声明

- 本审查对 `aistudio-api` 与 `fanout` 均为**只读**：未修改任何源码、测试、配置、UI 或契约；未执行 `git add` / `git commit` / `git push` / `git stash` / `git checkout`。
- 未启动真实服务（`main.py server`）、未拉起真实浏览器、未访问 VPS、未访问外网/Google、未使用任何账号 Cookie/凭据；未触发带账号凭据的请求。
- 唯一写操作：落盘本报告 `docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.8-exit-pool-special-mode.md`。
- 允许的 `GOOS=linux GOARCH=amd64 go build ./...` 在 fanout 仓库生成/更新了被 `.gitignore:1` 忽略的 `fanout` 二进制（构建产物，非 tracked 文件）；`git status --short` 确认除本报告外无其它新增/修改。
- 本报告的行号均基于当前工作区文件；若后续修复导致行号漂移，以文件内容与符号名为准。

---

## 七、二轮复审（返工验证）

- 复审日期：2026-11-23
- 复审性质：只读独立二轮，仅验证 A（app 侧 `exit_arbitration.py` / `exit_pool/{config,resolver}.py` / `routing_pipeline.py` / `api/app.py` / `engine_runtime.py` / `tests/conftest.py` / 两个单测）与 B（`data/tools/exit-manager/`）两路返工，不改任何源码/测试/配置。
- 复核基线：一轮报告结论 CONDITIONAL（1 P1 + 11 P2）。

### 7.1 二轮结论

**PASS（0 × P0 / 0 × P1 / 0 × P2，可进入下一阶段）**

一轮 1 个 P1 与 11 个 P2 已全部闭环；返工未引入新的 P0/P1/P2。默认（`AISTUDIO_EXIT_MODE` 缺失）路径仍无 resolver、无 HTTP 客户端、不改 `settings.proxy_url`；D-05 顺序与 `active_requests` 唯一并发账本未被破坏；无 `defaults/config.schema.json` / `web/` UI 改动，无新增第三方依赖。

### 7.2 逐项复核（对应复审清单 1–18）

| # | 项 | 结论 | 证据（file:line / 测试名 / 证据） |
|---|---|---|---|
| 1 | P1-1 `_auth_file` 接受 `Path` 与 `str`，且回归真正走通「Path store → 非 None → 发探针」 | **PASS** | `src/aistudio_api/application/exit_arbitration.py:112-116` 先将 `Path` 转 `str`；`tests/unit/test_exit_arbitration.py:756-785` `test_signer_canary_probe_accepts_path_and_str_store` 以真实 `pathlib.Path` 参数化 `[False, True]`，断言 `probe._auth_file() is not None`、`verdict == account_bad`、`len(runtime.calls) == 1`、`session.requests == [("[]", LIST_MODELS_URL)]`，非空转。 |
| 2 | P2-1 `healthy` 非 `true` → 拒绝 / 回退全局 / 不写缓存，覆盖 `false` 与缺失 | **PASS** | `resolver.py:298-308` `payload.get("healthy") is not True` 直接 `return None`（不 `_store`）；`tests/unit/test_exit_pool.py:283-303` `test_resolve_unhealthy_exit_falls_back_and_is_not_cached` 参数化 `[False, None]`，断言 `resolve` 为 `None`、`assignment_for` 与 `include_expired=True` 均 `None`。 |
| 3 | P2-4 canary 污染闩锁（verify/401/其他）生效、不泄露 Cookie、闩锁后不再建会话 | **PASS** | `exit_arbitration.py:95-98` 进程内闩锁；`:155-159` 先于一切判定；`:226-232` verify 优先、仅 `403 + region` 判 `exit_bad`，其余 `_mark_contaminated`；`:234-244` detail 仅含原因码不含 body。`test_signer_canary_pollution_latch_stops_further_probes`（`:813-867`）参数化 verify/401/500，断言 `first/second` 均 `canary-contaminated:<reason>`、`len(runtime.calls) == 1`、日志无 `secret`。 |
| 4 | P2-6 endpoint scheme/host 加载期校验，非法整体 off，无运行期降级 | **PASS** | `config.py:64-81` `_normalize_endpoint` 仅接受 `http/https` 且 host 非空；`:115-142` 非法即 `from_env()` 返回 `None`。`tests/unit/test_exit_pool.py:99-120` `test_invalid_endpoint_keeps_feature_off_at_load` 覆盖 `ftp://`、无 scheme、空 host、`socks5h://` 五态；运行期 `_fetch_assignment` 仅处理网络/5xx 降级，无二次 endpoint 校验路径。 |
| 5 | P2-7 shutdown 关闭并重置 resolver，异常不阻断退出 | **PASS** | `src/aistudio_api/api/app.py:252-263`：`aclose()` 包 `try/except Exception/finally reset_exit_resolver()`；`tests/unit/test_exit_pool.py:576-607` `test_shutdown_runtime_closes_and_resets_exit_resolver` 断言 `closed == [True]`、`resets == [None]`。异常分支由 `except Exception ... finally` 静态确认（`CancelledError` 属 `BaseException`，正确继续上抛）。 |
| 6 | P2-8 `invalidate_exit` 清该出口全部账号缓存/最后已知绑定，其它出口不受影响 | **PASS** | `resolver.py:188-209` 同时扫 `_cache` 与 `_last_known`；`exit_arbitration.py:345-348` 在 `exit_bad` 上报后调用。`tests/unit/test_exit_pool.py:306-340` `test_invalidate_exit_drops_every_account_on_that_exit` 断言 exit-1 的 acc-1/acc-3 清空、exit-2 的 acc-2 保留。 |
| 7 | P2-10 真实覆盖 busy / restart 失败 / 二次 exit_bad / 冷却 | **PASS** | `tests/unit/test_exit_arbitration.py:429-448` busy（`active_requests=1` 不 restart）、`:451-469` restart 抛错退化原 `AuthError`、`:472-503` 二次 exit_bad 不再重建（`len(probe.calls)==2`、`invalidated_exits==["exit-1","exit-1"]`）、`:506-523` pipeline 级冷却、`:563-579` 每出口冷却。 |
| 8 | P2-11 tier 检查前移，tier 不满足不消耗 canary/重建 | **PASS** | `src/aistudio_api/application/routing_pipeline.py:973-987`：tier 判定（`:973`）先于 region 仲裁（`:979-987`）。`test_region_403_tier_mismatch_raises_before_canary`（`:401-426`）断言 `TierRequiredError` 且 `probe.calls==[]`、`restart_calls==[]`、`resolver.events==[]`。 |
| 9 | Q-1/Q-2/Q-3/Q-6 | **PASS**（Q-1 tier 附注） | Q-1：`exit_arbitration.py:118-136` 读 `AccountMeta.auth_user`，`:189` 传入；`test_signer_canary_probe_uses_account_auth_user`（`:788-810`）断言 `auth_user=="1"`。Q-2：`:138-153` 运行期跳过 + `:396-410` 构建期 error/warn；`test_signer_canary_probe_skips_account_bound_to_a_slot`（`:870-895`）、`test_build_exit_arbitration_warns_when_canary_in_slot`（`:898-910`）、`..._slot_ids_unavailable`（`:913-926`）。Q-3：`:63-79` 说明 ListModels 走 `BrowserlessSession.send_hooked_request`→`SignerClient.send_rpc` 且不发 proof；生产签名 `browserless_session.py:704-737` 与探针调用 `body/url` 兼容；测试断言 body `[]` 与 `LIST_MODELS_URL`。Q-6：`test_media_or_non_generation_403_never_arbitrates`（`:526-557`）证明非 region 的 media 403 不触发、`record_attempts=False` 的 countTokens 不触发；region 标记只由生成主链 403 产生（`client.py:304,384` / `instance_client.py:171,209` / `streaming.py:241`），无独立 media 上传端点。 |
| 10 | N-4 conftest 清理 `AISTUDIO_EXIT_*` 且不误伤 | **PASS** | `tests/conftest.py:7-28` 自动 fixture 逐项 `delenv` 并在前后 `reset_exit_resolver()`；全量 `pytest` 1815 全绿（见 §7.4），未出现相互污染。 |
| 11 | 管理器 P2-2 隔离状态与事件摘要同一次原子写；崩溃/重复语义正确 | **PASS** | `manager.py:384-410` digest 与 `status/last_error/quarantined_at` 同处 `_save_exits()`（同一 `exits_state.json` 原子写）；`record_event` `:625-658`。测试 `test_exit_bad_persists_status_and_digest_in_one_write`（`test_manager_events.py:100-116`）读盘断言同文件同时含 `quarantined` 与 1 条 `recent_events`，重启后判 `duplicate` 且保持隔离；`test_event_replays_when_isolation_persisted_without_digest`（`:118-141`）覆盖旧状态“已隔离但无摘要”重放→dedup；`test_duplicate_event_is_idempotent_and_persisted`（`:87-98`）。 |
| 12 | 管理器 P2-3 `rotate_on_isolation` 默认 false，显式才轮换，DEPLOY 一致 | **PASS** | `config.py:64` 默认 `False`、`:271-287` 缺省 `False`；`manager.py:419-427` `should_rotate` 要求该开关且 `was != QUARANTINED`。`config.example.json:22` false；`DEPLOY.md:13,255,302` 明确“默认只迁移不重启 WARP”。测试 `test_rotate_on_isolation_defaults_false`（`test_config.py:38-40`）、`test_quarantine_does_not_rotate_by_default`（`test_manager_assign.py:263-266`）、`test_exit_bad_rotates_only_when_explicitly_enabled`（`test_manager_events.py:55-69`）。 |
| 13 | 管理器 P2-5 exits/warps id 加载期同一正则校验 | **PASS** | `config.py:21` 定义 `EXIT_ID_RE`，`:172-175` exits、`:416-419` warps 均校验；`manager.py:17` 复用同一常量、`:230-233` 运行期校验。测试 `test_exit_id_must_match_runtime_regex`（`test_config.py:42-54`）、`test_warps_id_must_match_runtime_regex`（`:56-62`）。 |
| 14 | 管理器 P2-9 `probing` 加载归一化，不会永久卡死 | **PASS** | `manager.py:119-134` 有错误/隔离时间→`QUARANTINED`，否则→`HEALTHY` 并清计数。测试 `test_probing_with_last_error_normalizes_to_quarantined`、`test_probing_without_error_normalizes_to_healthy`、`test_normalized_probing_exit_can_be_recovered`（`test_manager_events.py:229-272`）。 |
| 15 | 管理器 Q-5 绑定 kind 稳定、容量统计一致 | **PASS** | `manager.py:314-348`：已有健康绑定沿用 `bound_kind`；迁移 `:329-341` 用 `bound_kind`；`_pick_exit` `:243-263` 同时统计 total 与 per-kind。测试 `test_existing_binding_keeps_original_kind`（`test_manager_assign.py:84-101`）、`test_migration_preserves_original_kind`（`:103-128`）、`test_capacity_by_kind_limits_per_kind`（`:68-82`）。 |
| 16 | 新增 `GET /v1/status`：Bearer + loopback/CIDR、GET-only、字段白名单、迁移后 `last_switch_at` | **PASS** | `server.py:120-123` peer 过滤、`:144-146` Bearer、`:163-167` 仅 GET 且返回 `status_summary`；`manager.py:758-773` 仅 `manager_ok/exits{total,healthy,quarantined,disabled}/bound_accounts/last_switch_at`，无 `socks_url`/token/出口 id/账号。测试 `test_status_requires_token`、`test_status_rejects_post`、`test_status_returns_whitelisted_summary_only`（`test_server.py:267-297`，断言无 `socks`/TOKEN/账号/`warp-1`）、`test_status_reports_last_switch_after_migration`（`:142-147`）、`ClientFilterTest.test_disallowed_client_is_403_even_with_valid_token`（`:150-164`）。 |
| 17 | 回归：off 模式无感、D-05 与唯一账本、无 schema/UI、无新依赖、格式基线 | **PASS**（格式 N-1 附注） | off：`test_exit_pool.py:74-76,473-521`、`test_exit_arbitration.py:240-264,929-949`。D-05：`routing_pipeline.py:1246-1308` 先 `release`→`active_requests==0`→`restart`；`instance_pool.py:139-158` `release()` 幂等（`_released` 守卫）；`instance_manager.py:582-593` restart 在锁内二次校验 busy；`_instance_active_requests`（`:1493-1501`）唯一账本。`git diff --name-only` 仅 6 个 src/test 文件，无 `web/`/`defaults/config.schema.json`；依赖仅既有 `httpx[socks]`（`pyproject.toml:14`）。格式差异见 N-1。 |
| 18 | 测试质量抽查（≥5 个关键用例，断言不空转、fake 与生产返回类型一致） | **PASS** | 手工审阅 6 个：`test_signer_canary_probe_accepts_path_and_str_store`（真实 `Path`）、`test_signer_canary_pollution_latch_stops_further_probes`（`calls==1`+日志脱敏）、`test_resolve_unhealthy_exit_falls_back_and_is_not_cached`（`false/None`+不缓存）、`test_exit_bad_restart_failure_degrades_to_original_error`（`restart_calls==["inst-1"]`+原错误）、`test_exit_bad_persists_status_and_digest_in_one_write`（读盘断言）、`test_status_returns_whitelisted_summary_only`（字段全集断言）。`_FakeStore.get_auth_path_optional` 现返回真实 `Path`（与生产 `Path \| None` 对齐，根治 P1 的类型漂移）；`_FakeResolver.assignment_for` 返回 `ExitAssignment \| None`、`_FakeProbe.probe` 返回 `CanaryVerdict`，与生产契约一致。 |

### 7.3 残留 Q / N（不阻塞）

- **Q-1b（沿用，非缺陷）**：`create_canary_session` 仍以 `tier="Free"` 默认值建 canary 会话（`engine_runtime.py` 的 `tier: str = "Free"`，探针未传 `tier`），只修了 `auth_user`。若 canary 为 Pro 号，signer 的 `g1_tier` 头会按 Free 发送；对 `ListModels` 探针影响有限，但语义未完全对齐。属一轮 Q-1 的剩余细节，建议后续按 canary 账号 `tier` 透传。
- **Q-6 说明**：本仓媒体以内联 `inlineData` 随 `GenerateContent` 提交，无独立上传/预上传端点；因此带 region 标记的生成 403 会（且应）触发出口仲裁，`test_media_or_non_generation_403_never_arbitrates` 覆盖的是“非 region 的 media 403”与“countTokens 不仲裁”两态。判定符合现架构，无需改动。
- **N-1（格式基线）**：`uv run ruff format --check .` 本轮为 `62 files would be reformatted, 349 files already formatted`（exit 1），比一轮的 61 多 1。经逐文件定位，唯一新增项是本审查报告自身 `docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.8-exit-pool-special-mode.md`（`docstring-code-format=true` 下其 Python 代码块被计入）。返工的 7 个 Python 文件全部 `already formatted`（见 §7.4），故该 +1 为审计文档产物、非 A/B 代码回归，不计 P。
- **N-2（fanout 未改动）**：`git -C D:\\LLM\\self_programming\\fanout status --short` 与一轮一致（`M health.go/main.go/tunnel.go`，`?? health_test.go/readonly.go/readonly_test.go`）；本轮 A/B 返工未触碰 fanout，按只读约定未重复交叉构建。

### 7.4 实跑门禁真实输出

均在 `D:\\LLM\\self_programming\\aistudio-api` 执行，未修改 tracked 文件、未启动服务/浏览器、未联网、未使用账号凭据。

```text
$ git status --short
 M src/aistudio_api/api/app.py
 M src/aistudio_api/application/routing_pipeline.py
 M src/aistudio_api/domain/errors.py
 M src/aistudio_api/infrastructure/gateway/instance_pool.py
 M src/aistudio_api/infrastructure/waa/engine_runtime.py
 M tests/conftest.py
?? docs/dev/rebuild/domains/P1-account/research/exit-pool/
?? docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.8-exit-pool-special-mode.md
?? src/aistudio_api/application/exit_arbitration.py
?? src/aistudio_api/infrastructure/exit_pool/
?? tests/unit/test_exit_arbitration.py
?? tests/unit/test_exit_pool.py

$ uv run ruff check .
All checks passed!
EXIT=0

$ uv run ruff format --check src/aistudio_api/infrastructure/exit_pool \
    src/aistudio_api/application/exit_arbitration.py \
    tests/unit/test_exit_pool.py tests/unit/test_exit_arbitration.py tests/conftest.py
7 files already formatted
EXIT=0

$ uv run ruff format --check .
62 files would be reformatted, 349 files already formatted
EXIT=1
# 唯一新增项 = 本审查报告 .md 自身（见 N-1）；7 个返工 Python 文件均 clean。

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations
EXIT=0

$ uv run pytest -q
1815 passed in 51.58s
EXIT=0

$ uv run pytest tests/unit/test_exit_pool.py tests/unit/test_exit_arbitration.py -q
69 passed in 0.71s
EXIT=0

$ python -m unittest discover -s data/tools/exit-manager/tests -v
...
Ran 111 tests in 3.683s

OK
EXIT=0
```

### 7.5 只读声明（二轮）

- 本轮对 `aistudio-api` 与 `fanout` 均为**只读**：未修改任何源码、测试、配置、UI 或契约；未执行 `git add` / `git commit` / `git push` / `git stash` / `git checkout`。
- 未启动真实服务（`main.py server`）、未拉起真实浏览器、未访问 VPS、未访问外网/Google、未使用任何账号 Cookie/凭据；未触发带账号凭据的请求。
- 唯一写操作：在本报告末尾追加本节「七、二轮复审」。
- 本节行号均基于当前工作区；若后续改动导致漂移，以文件内容与符号名为准。
