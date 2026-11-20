# REV-2.1.5.6-discovery-fallback-removal-and-signer-tuning — 模型发现磁盘兜底删除、signer 生存期/DNS/visit-id 收敛与 direct_http 警示独立审查报告

- **审查对象**：基线 `e2c8843` 之后的未提交工作区改动（批次 C：C1 模型发现磁盘兜底删除；C2 signer idle TTL 默认 900→3600 双侧对齐；C3 DNS 兜底列表收敛；C4 visit-id 侧车复用；C5 direct_http 进程级警示）
- **审查角色**：独立只读 Reviewer（与 Worker 零上下文共享）
- **审查日期**：2026-11-20
- **审查环境**：Windows / pwsh；pytest 前已设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`；未联网、未 ssh、未启动生产服务
- **最终判定**：**CONDITIONAL**

> **只读声明**：本次审查仅执行 `git` / 文件读取 / 静态搜索 / 单元测试 / Go 构建测试等只读或离线操作，除本报告外未修改任何源码、测试、配置、前端产物或文档；未执行 `git add`、`git commit`、`git push`；未启动 `main.py server` 或真实浏览器。

---

## 1. 变更盘点

`git status --short` 仅显示 17 个已修改文件，无新增/删除/重命名/未跟踪文件；与 Worker 声称的改动集合一致，且未触碰 `REV-*.md`、`docs/dev/rebuild/**`、`AGENT.md`、`web/**`、`routes_accounts.py`、`cookie_parser.py`、`browserless_session.py`。

| 模块 | 文件 | 变更摘要 |
|---|---|---|
| C1 发现降级 | `src/aistudio_api/infrastructure/gateway/model_discovery.py` | 删除 `live_http` 磁盘兜底、`_HttpCandidate`/`_account_http_candidates`/`_fetch_via_http`/`_active_rotator`/`_auth_locked` 及死导入；`LIVE_SOURCES`、`DISCOVERY_TRUTH_SOURCES` 收敛 |
| C1 配置口径 | `src/aistudio_api/infrastructure/gateway/model_defaults.py`、`defaults/config.yaml` | 注释与刷新来源说明移除 `live_http` |
| C2 signer 生存期 | `waa-signer/cmd/waa-signer/main.go`、`waa-signer/README.md`、`defaults/config.yaml`、`defaults/config.schema.json`、`model_defaults.py` | `idle_ttl` 默认值 900→3600；`max_workers` 保持 8 |
| C3 DNS | `waa-signer/cmd/waa-signer/main.go`、`main_test.go` | 兜底 DNS 由 `8.8.8.8,1.1.1.1,223.5.5.5` 收敛为前两者 |
| C4 visit-id | `waa-signer/internal/aistudio/runtime_go.go`、`runtime_go_test.go` | 新增 `loadOrCreateVisitID` 侧车原子读写并接入冷启动 |
| C5 direct_http 警示 | `src/aistudio_api/infrastructure/waa/engine_runtime.py`、`tests/unit/test_engine_runtime_lifecycle.py` | 进程级一次 WARNING 与用例 |
| 测试迁移 | `tests/unit/test_model_discovery_browserless.py`、`test_model_discovery_live_wiring.py`、`test_model_warmup_refresh.py`、`test_routes_models.py`、`test_routes_system.py`、`test_ui_debug_sandbox.py` | 删除磁盘 HTTP 兜底用例，新增结构守卫与降级语义断言 |

`git diff --check e2c8843` 退出码 0；diff 未发现调试 `print`、`console.log`、`debugger`、TODO/FIXME 或明文密钥残留。

---

## 2. 逐项审查

### 2.1 C1：磁盘兜底删除面与降级链（通过）

**删除面 100% 干净**：对 `src/**/*.py` 搜索 `_fetch_via_http|_account_http_candidates|_HttpCandidate|_http_candidate_key|live_http|_active_rotator|_auth_locked|MAX_HTTP_ACCOUNT_CANDIDATES`，仅命中 `application/account_rotator.py:658` 与 `application/account_keepalive.py:202,212,417,601,610,716` 的同名业务方法（保活/轮转自身的 auth-lock 判定），与被删的模型发现模块级辅助函数无关；模型发现文件已无任何 `live_http` 或裸 `httpx` 引用。

**关键删除点**：
- `model_discovery.py:537`：`LIVE_SOURCES = frozenset({"live_page", "live_signer"})`。
- `model_discovery.py:547`：`DISCOVERY_TRUTH_SOURCES = frozenset({"live_page", "live_signer", "snapshot"})`。
- `model_discovery.py:691-698`：`get_models` 删除 `cookies`/`auth_user` 形参。
- `model_discovery.py:749-766`：候选汇总只保留池会话，注释明确“不存在磁盘 auth.json 兜底”。
- `model_discovery.py:803-835`：无 signer 候选时记录 `no discovery credentials available`；候选总数只统计 `signer_sessions`。
- 原 3b 磁盘 HTTP 分支（`_fetch_via_http` 循环）已整体删除。

**调用方签名兼容性逐点核对**：全库 `get_models(` 调用点为
`api/routes_system.py:404`、`api/routes_openai.py:225`、`api/routes_models.py:106`、`api/app.py:137`、`model_discovery.py:985`（`get_model_by_id` 内部转发）以及测试中的 fake/直接调用；全部使用 `sessions=` / `session=` / `force_refresh=` / `reset_backoff=`，无任何 `cookies=` 或 `auth_user=` 传参，删除死参无兼容性破坏。

**降级链完整性**：
1. 退避门：`model_discovery.py:717-729`，命中则 `_ensure_fallback_cache`（`:620-629`）。
2. 快照加载与脏快照守卫：`_load_snapshot`（`:636-672`），`_snapshot_is_dirty`（`:529-534`）对测试假名与 `<5` 有效模型回退内置表。
3. 快照保留：全候选失败时 `:853-871` 仅在 `not self._cached_models` 时写入 `_FALLBACK_MODELS`，否则保留快照。
4. 内置表：`_FALLBACK_MODELS` 兜底仍可达，`_ensure_fallback_cache` 与 `get_models` 第 4 步双入口均验证。

**来源集合一致性**：`LIVE_REFRESH_SOURCES` 未变（`:542`）；`api/app.py:150-158` 仅消费该集合，`live_http` 不在其中，预热间隔逻辑无需改动。`LIVE_SOURCES`/`DISCOVERY_TRUTH_SOURCES` 无其他生产消费点（`DISCOVERY_TRUTH_SOURCES` 仅经 `discovery_entry_is_truth`，`:567-569`），删除 `live_http` 安全。旧快照若曾带 `_discovery_source=live_http`，`_load_snapshot` 会把整批条目重新标记为 `snapshot`（`:664`），不会形成新的 truth 盲区。

### 2.2 C2：3600 的“源码一致”通过，但“生产生效”不成立（P1）

**源码四处一致（通过）**：
- `waa-signer/cmd/waa-signer/main.go:48`：`defaultIdleTTL = 3600 * time.Second`。
- `defaults/config.yaml:39`：`idle_ttl_s: 3600`。
- `defaults/config.schema.json:113-116`：`minimum: 1, default: 3600`，无上限封顶，3600 合法。
- `model_defaults.py:746-749`：`signer.get("idle_ttl_s", 3600)`。
- `waa-signer/README.md:84-86`：默认值与参数示例同步为 `3600s`。
- `max_workers` 五处均保持 `8`，未被本次改动触碰。

**生产链路复核（不通过）**：归一化结果把 signer/transport 放在 `engine.browserless` 下，而运行时按顶层键读取，导致 3600 从未注入实际启动的 signer 子进程。

链路证据：
1. `model_defaults.py:812-834` 返回 `{"mode": ..., "switch": ..., "browserless": {"signer": {... "idle_ttl_s": 3600 ...}, "transport": {...}}}`，即 `signer` 是嵌套键。
2. `api/app.py:399` 取得该块，`:418-420` 原样传给 `build_engine_runtime(engine_settings, ...)`；热切换路径 `routes_system.py:690` 的 `validate_engine_settings` 返回同样嵌套结构。
3. `engine_runtime.py:137-142` 却读取 `engine_settings.get("signer")` / `engine_settings.get("transport")`；喂入归一化块时二者均为 `None`，`_signer_settings` / `_transport_settings` 退化为空字典。
4. `engine_runtime.py:198` 因此命中构造器兜底 `idle_ttl_s=900`；`local_daemon.py:106-107` 又把 `-idle-ttl 900s` 显式传给 signer 进程，覆盖 Go 侧新的 3600 默认值。

实测复现（只读、未启动进程）：用 `get_configured_engine_settings()` 的返回值构造 `BrowserlessEngineRuntime` 后，`_signer_settings` 与 `_transport_settings` 均为 `{}`，`_ensure_daemon().idle_ttl_s` 为 `900.0`，`max_workers` 仅因默认值碰巧为 8 而看似正确。测试统一使用扁平形状（`test_engine_runtime_lifecycle.py:64-78`），因此没有覆盖真实配置形状。

影响：
- C2 的“延长保温以降低冷启动风暴频率”在当前生产链路**完全不生效**；Go 二进制默认 3600 只对未传 `-idle-ttl` 的手工启动有效。
- 同一形状问题还使 `signer.endpoint`、`binary_path`、`state_dir`、`request_timeout_s`、`transport.mode`、`http2`、`max_connections` 等生产配置被忽略；其中多数默认值碰巧与默认配置一致，但 `direct_http` 配置同样无法生效，进而 C5 的警示在生产路径不可触发。
- 该形状不匹配是基线 `e2c8843` 之前既有的结构问题，并非本批次新引入；但本批次 C2/C5 的交付目标依赖它，必须在本轮清零。

**运行态覆盖（P1 附带）**：仓库根 `config.yaml`（untracked + gitignored，`:21`）显式写有 `idle_ttl_s: 900.0`。即使修复形状问题，当前运行态仍会覆盖 `defaults/config.yaml` 的 3600。该文件不属于提交内容，但部署前必须同步改为 3600 或删除该键，否则改动对当前实例无效果。

### 2.3 C3：DNS 收敛（通过）

- `main.go:900`：`defaultFallbackDNSServers = "8.8.8.8:53,1.1.1.1:53"`。
- `main_test.go:104-115`：linux/android 在 `/etc/resolv.conf` 缺失或为空时断言恰为这两项；存在非空 resolv.conf 时仍返回 `nil`（不侵入系统解析器）；显式 `-dns` 覆盖仍生效。
- `main.go:1044-1056`：仅 linux/android 且 resolv.conf 缺失时使用该兜底；windows/darwin 保持系统解析器，不受影响。
- `223.5.5.5` 仅剩解析器单元测试中的输入样例，不再作为默认兜底，断言与实现一致。

### 2.4 C4：visit-id 侧车（通过，附 Q）

- `runtime_go.go:177-212`：侧车路径为 `filepath.Join(filepath.Dir(storageStatePath), "visit-id")`，即每个账号目录一份；`main.go:595` 的 `storage-state.json` 位于 `accDir`，目录在冷启动前已创建。
- 读取到非空值即复用（`strings.TrimSpace` 兼容行尾空白）；缺失或空值才生成新 ID。
- 写入路径为同目录 `os.CreateTemp` + `WriteString` + `Close` + `os.Rename`，失败分支统一删除临时文件并仅 `slog.Debug`，返回新值而不阻断冷启动；`newVisitID` 自身失败才向上返回错误（随机源故障，合理）。
- `runtime_go.go:251` 已从 `newVisitID()` 切换为 `loadOrCreateVisitID(options.StorageStatePath)`。
- 单测 `runtime_go_test.go:49-96`：用例一验证已有侧车（含换行）被复用；用例二验证首次生成 `v1_` 前缀、精确落盘、二次调用复用同一值，具备真实断言而非快照式空转。

Q 级补充：未覆盖“侧车目录不可写/重命名失败仍返回新 ID”的失败路径，也未覆盖同账号并发冷启动的竞态（两个 Worker 各生成一个值、后写者胜出）。当前单进程串行冷启动场景可接受。

### 2.5 C5：direct_http 进程级警示（代码通过，生产触达受 P1 影响）

- `engine_runtime.py:37-38` 进程级 `_direct_http_warned`；`:212-219` 仅在 `transport.mode == "direct_http"` 时首次 `preflight` 输出 WARNING，文案明确“Python TLS 裸直连降级 / 指纹暴露与上游风控风险 / 建议 signer_rpc”，与 `browserless_session.py` 的实际 direct_http 行为一致。
- `test_engine_runtime_lifecycle.py:195-217` 通过 `monkeypatch` 重置标志，构造两个 runtime 并连续 `preflight`，断言 `direct_http` 相关 WARNING 恰好 1 条且包含 `signer_rpc`，去重语义为“每进程一次”，不是“每次构造一次”。
- 受 2.2 的嵌套/扁平问题影响，生产配置的 `transport.mode=direct_http` 目前不会进入 `_transport_settings`，警示与 direct_http 通道本身都不会被真实启用；修复 2.2 后本项自然恢复。

---

## 3. 测试迁移真实性

逐条核对迁移后的断言强度：

| 用例 | 旧语义 | 新语义 | 判定 |
|---|---|---|---|
| `test_signer_401_without_other_channel_degrades_to_builtin` | 401 后靠磁盘 HTTP 切下一个健康账号，断言 `live_http` | 401 后无第二通道，断言 `last_source=="fallback"`、`last_error` 含 401、signer 仅调用 1 次、`_fetch_via_http` 不存在 | 不弱于旧语义，准确反映删除后的唯一路径 |
| `test_live_signer_success_atomically_writes_cache` | 原名 `live_http_success`，走 HTTP 写缓存 | 改走 signer 200 响应，断言版本、时间戳、模型 ID 与无 `.tmp.` 残留 | 覆盖目标从 HTTP 切到 signer，原子写断言保留 |
| `test_disk_http_fallback_surface_is_removed` | 无 | `inspect.getsource` + `hasattr` 双重守卫：方法/候选/数据类/来源集合全部不存在，源码无 `import httpx`/`httpx.AsyncClient` | 结构守卫，防止死路复活 |
| `test_all_candidates_fail_keeps_previous_valid_cache` | 依赖第二账号 HTTP 失败 | 只走 signer 401，断言快照原样返回、失败计数/退避推进、磁盘快照字节不变 | 快照保留语义未被削弱 |
| `test_backoff_gate_returns_cache_without_any_network_even_when_forced` | 以 `_BombStore` 证明不读账号 | 断言 `_account_http_candidates` 不存在 + signer sender 抛错，证明退避门零网络 | 等价且更贴近删除后的实现 |
| `test_routes_models.py::test_model_discovery_never_uses_naked_httpx_disk_fallback` | 旧用例断言页面未就绪时读 auth.json 并调用 `_fetch_via_http` | 断言 `page=None` 的无凭据会话降级 `fallback`、模型数 ≥30、无 `_fetch_via_http`/`_account_http_candidates`、源码无 httpx | 由“证明兜底存在”改为“证明兜底不存在”，符合本批次目标 |
| `test_model_warmup_refresh.py` | 非实时来源含 `live_http` | 参数化收敛为 `fallback`/`snapshot` | 与实际来源集合一致 |

用例数量核对：对本次改动的 7 个测试文件按函数与 `parametrize` 字面量展开统计，基线 109 → 当前 107，净减 **2**，与 Worker 自报一致；全量套件实跑 **1745 passed**。净减来自磁盘 HTTP 专项用例删除与 warmup 参数项减少，新增结构守卫 1 条，原因可解释。

---

## 4. 门禁独立实跑

| 门禁 | 命令 | 实际结果 |
|---|---|---|
| Ruff Lint | `uv run ruff check .` | `All checks passed!`（exit 0） |
| Ruff 目标文件格式 | `uv run ruff format --check`（本次改动的 10 个 Python 文件） | `10 files already formatted`（exit 0） |
| Ruff 全仓格式 | `uv run ruff format --check .` | `61 files would be reformatted`（exit 1）；61 个文件与本次改动文件集合交集为空，确认全仓格式基线红，非本批次回归 |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`（exit 0） |
| Pytest | `uv run pytest -q`（UTF-8 环境） | `1745 passed in 52.72s`（exit 0） |
| Go 构建 | `go build ./...`（CGO_ENABLED=1，Zig 0.13.0 + SpiderMonkey windows-amd64 头/库） | exit 0 |
| Go 测试 | `go test ./...` | 全部 `ok`；`cmd/waa-signer` 0.249s、`internal/aistudio` 0.224s，其余 cached，exit 0 |
| gofmt | `gofmt -l .`（`waa-signer`） | 无输出（exit 0） |
| Diff 空白 | `git diff --check e2c8843` | 无输出（exit 0） |

未运行前端构建：本次 diff 无 `web/**` 变更；前端仅存在不可达的 `live_http` 标签（见 P2）。

---

## 5. 隐私扫描

对本轮完整 `git diff e2c8843` 检索本机盘符路径、私有网段样例、常见邮箱域名等模式，命中 **0**；本报告同样未包含上述内容。diff 中无明文密码、token、API Key 或 Cookie 材料。

---

## 6. 问题清单

### P0

- 无。

### P1

- **P1-1（C2 生产不生效，阻断清零）**：`model_defaults.py:812-834` 产出嵌套 `engine.browserless.signer/transport`，`app.py:399,418` 原样传入，`engine_runtime.py:137-142` 却按顶层 `signer`/`transport` 读取，导致 `_signer_settings`/`_transport_settings` 为空；`engine_runtime.py:198` 回落到 900，`local_daemon.py:106-107` 再以 `-idle-ttl 900s` 显式覆盖 Go 的 3600。实测 `_ensure_daemon().idle_ttl_s == 900.0`。同一原因使 `transport.mode=direct_http` 及 endpoint/timeout 等配置在生产被忽略，C5 警示与 direct_http 通道无法触达。建议在 `build_engine_runtime` 将 `engine_settings["browserless"]` 展平为顶层 `signer`/`transport`，或让运行时直接读取嵌套块；并新增一个以 `get_configured_engine_settings()` 真实形状构造 runtime、断言 `idle_ttl_s == 3600` 的回归用例。该问题为预存在结构缺陷，但直接使本批次 C2 目标落空。
- **P1-2（运行态覆盖）**：仓库根 `config.yaml:21` 仍为 `idle_ttl_s: 900.0`。该文件 untracked 且被 gitignore，不在 diff 中，但当前运行实例会覆盖 `defaults/config.yaml` 的 3600；部署/重启前必须同步为 3600 或删除该键。

### P2

- **P2-1（前端不可达残留）**：`web/src/views/SettingsView.vue:266,282` 仍保留 `live_http: '实时接口（直连兜底）'` 与成功分支判断；`web/src/components/config/schema.ts:1026` 描述仍写 `fallback/snapshot/live_http`。C1 后 `last_source` 不可能为 `live_http`，属不可达兜底，但文案具有误导性，建议后续清理。Worker 已主动申报。
- **P2-2（域文档漂移）**：`docs/dev/rebuild/domains/P5-generation/index.md` 的域职责仍写“`live_page`→`live_http` 依次重试”，与当前实现不符；不在本次改动范围，建议随文档批次修正。
- **P2-3（构造器默认漂移）**：`engine_runtime.py:198` 与 `local_daemon.py:60` 的 `900` 兜底仍保留，与归一化默认 3600 不一致。若仅作为防御性兜底可接受，但在 P1-1 修复后应统一为 3600，避免未来以未归一化 dict 构造 runtime 时再次静默回到 900。

### Q

- **Q-1**：`loadOrCreateVisitID` 的侧车不可写/rename 失败路径与同账号并发冷启动竞态无测试覆盖；当前单进程串行场景风险低。
- **Q-2**：`_direct_http_warned` 为无锁模块全局；引擎热切换串行执行，竞态仅可能产生重复 WARNING，不影响正确性。

### N（说明，不作为缺陷）

- **N-1**：`test_ui_debug_sandbox.py` 中两处与本批次无关的格式化折叠由 `ruff format` 产生，Worker 已申报，无语义变化。
- **N-2**：根 `config.yaml` 为本地运行态文件，按仓库规范不入库；本次不作为提交内容审查。

---

## 7. Worker 七条偏离逐条裁决

| # | 偏离 | 裁决 | 依据 |
|---|---|---|---|
| 1 | `ruff format` 全仓基线红 | **接受** | 实跑 61 个文件待格式化，与本次改动 0 交集；目标 10 文件全部已格式化 |
| 2 | `get_models` 死参清理 | **接受** | 全库调用点均为关键字调用，无 `cookies`/`auth_user` 依赖 |
| 3 | `engine_runtime:198` 与 `local_daemon.py:60` 构造器兜底仍 900 | **有条件接受，升级为 P1** | 单独看是防御性兜底；但与嵌套/扁平形状缺陷叠加后，生产实际注入 900，直接抵消 C2；不能按“无害偏离”放过 |
| 4 | `app.py` 未改 | **接受** | app 仅消费 `LIVE_REFRESH_SOURCES`，无 `live_http` 分支；C1 改动无需触碰 |
| 5 | `test_routes_system.py` 追加 `live_signer` 透传 | **接受** | 来源替换诚实，断言 `{"source": "live_signer", "model_count": 1}` 与路由行为一致 |
| 6 | `test_ui_debug_sandbox.py` 附带格式化 | **接受（P2 卫生）** | 无语义变化，已申报；后续建议单独格式化批次 |
| 7 | web 侧 `live_http` 映射保留 | **接受（P2 残留）** | 运行时不可能产生该来源，属不可达兜底；但文案应后续清理 |

---

## 8. 结论

- C1（磁盘兜底删除）、C3（DNS 收敛）、C4（visit-id 侧车）、C5（警示代码与用例）实现正确，删除面无悬挂引用，降级链完整，测试迁移未削弱旧断言；全量 pytest 1745、Go build/test、gofmt、ruff/pyright 均通过，隐私与空白检查干净。
- C2 仅完成“源码默认值对齐”，但生产链路因 `engine.browserless` 嵌套结构与运行时顶层读取不匹配，实际仍向 signer 传 `-idle-ttl 900s`；叠加本地运行态 `config.yaml:21` 的 900.0 覆盖，3600 保温目标未达成。该问题同时使 C5 的 direct_http 警示在生产不可触达。
- 因此本批次**不能判定 PASS**，结论为 **CONDITIONAL**：需修复 P1-1（展平或读取嵌套块，并补真实形状回归测试）与 P1-2（同步运行态配置）后重新复审；P2/Q 可在后续维护批次清理。当前仍存在**新的可裁决遗留**（P1-1/P1-2），未达到“可裁决清零”。

---

# 二轮复审（返工核验）— 2026-11-20

- **复审对象**：一轮 CONDITIONAL 后 Worker 对五项可自裁项的返工
- **复审角色**：同一独立只读 Reviewer（无 Worker 上下文共享）
- **复审环境**：Windows / pwsh；pytest 设置 UTF-8；未联网、未 ssh、未启动生产服务
- **最终判定**：**PASS**（P0/P1 清零；剩余 P2/Q 均非阻断，见第 10 节）

> **只读声明**：二轮仅读取工作区、执行离线测试与构建，除本报告追加内容外未修改任何源码、测试、配置、前端或静态产物；web 构建输出到系统临时目录并已清理；未执行 `git add`/`commit`/`push`。

## 9. 五项返工闭环核验

| 项 | 返工内容 | 核验结果 | 判定 |
|---|---|---|---|
| ① P1-1 配置形状 | 改读取侧，`engine_runtime.py:137-144` 从 `engine_settings["browserless"]` 取嵌套 `signer`/`transport`；新增真实形状回归用例 | 读取侧只认嵌套形状，无顶层扁平回退/Shim；全部 `BrowserlessEngineRuntime(...)` 测试夹具均为嵌套；`test_engine_runtime_lifecycle.py:231-264` 以 `validate_engine_settings` 归一化产物构造 runtime，断言 daemon、命令行、transport 与警示 | **CLOSED** |
| ② P1-2 运行态 | 根 `config.yaml:21` 改为 `idle_ttl_s: 3600`；声称部署清单注明 VPS 需同步 | 根运行态已确认 3600；仓库 tracked diff 中未找到单独的“VPS 需同步”部署清单，README 仅有 `max_workers`/VPS 内存提示 | **运行态 CLOSED；文档提示 Q-4** |
| ③ P2-1 web 文案 | `SettingsView.vue` 删除 `live_http` 标签/成功分支；`schema.ts:1026` 删除来源名；`bun build` 更新产物 | Vue 源码 `live_http` 计数 0；编译后 `SettingsView.js` 计数 0、旧文案计数 0；lint/type-check/build 均通过；除静态 `index.html` 空白行外，构建产物与仓库文件逐文件哈希一致 | **CLOSED（附 P2-4 产物清理）** |
| ④ P2-2 域文档 | P5 `index.md` 仅修正一行模型发现口径 | `git diff` 仅一行：`live_page→live_http 依次重试` 改为 `live_page/live_signer 双通道`，无其他内容变动 | **CLOSED** |
| ⑤ P2-3 兜底默认 | `engine_runtime.py:200`、`local_daemon.py:60` 的 900 改 3600 | 两处均为 3600；生产归一化实测 `idle_ttl_s == 3600.0`，命令行为 `-idle-ttl 3600s` | **CLOSED** |

### 9.1 P1-1 修复方向与全链回归

- 归一化产物仍是嵌套结构：`model_defaults.py:812-834` 返回 `engine.browserless.signer/transport`；`app.py:399,418`、热切换 `routes_system.py:690` 与 `EngineSwitcher` 均原样传递该结构，读取侧统一为嵌套是正确的最小切口，未引入“先试扁平再试嵌套”的双形状兼容层。
- `test_normalized_engine_settings_reach_daemon_and_direct_http_warning` 断言链完整：归一化 → `runtime._ensure_daemon()` 的 `idle_ttl_s == 3600.0` 与 `_command()` 中 `3600s` → `_signer_settings["request_timeout_s"] == 300.0`、`_endpoint()` 默认值 → `_transport_settings["mode"] == "direct_http"`、`read_timeout_s == 300.0` → `preflight()` 触发 direct_http WARNING。
- **测试强度补充说明**：由于 P2-3 已把构造器兜底也改为 3600，单看 `idle_ttl_s == 3600.0` 无法区分“配置直达”与“读取失败后命中兜底”；但同一用例对 `request_timeout_s`、`transport.mode` 的键访问与警示断言会在读取失败时直接失败，因此整体具备牙。为彻底消除歧义，二轮另以非默认值独立验证：`idle_ttl_s=4321`、`read_timeout_s=321`、`transport.mode=direct_http` 经归一化后，daemon 参数为 `4321s`、读取值为 `321.0`/`direct_http`。建议后续把回归用例的 idle 值改为非默认值（Q-3，非阻断）。

### 9.2 P1-2 与部署提示

- 根运行态 `config.yaml:21` 已为 `idle_ttl_s: 3600`，生产归一化实测不再回落到 900。
- 未在 tracked 文件中找到“VPS 需同步”的独立部署清单；`waa-signer/README.md` 仅新增 `max_workers` 与 VPS 内存的提示。该项若属于外部 VPS 清单，无法从仓库独立验证，记为 Q-4；不影响代码/运行态闭环。

### 9.3 P2-1 产物一致性

- `web/src/views/SettingsView.vue` 与 `web/src/components/config/schema.ts` 均无 `live_http`；`src/aistudio_api/static/assets/SettingsView.js` 无 `live_http` 与旧标签文案，保留 `live_signer` 成功分支。
- `bun run lint`、`bun run type-check` 通过；`bun --bun vite build`（输出到临时目录）成功，1800 模块转换；临时构建与仓库静态目录逐文件哈希比对，仅 `index.html` 不一致。
- **P2-4（新发现，非阻断）**：仓库 `src/aistudio_api/static/index.html` 相对基线仅删除一个空行；二轮两次独立构建（`bun --bun x vite build` 与 `bun --bun vite build`，均输出到临时目录）都会保留该空行，且当前文件混有 1 个 CRLF。说明该文件不是当前源码构建的规范产物，建议提交前恢复该空行/重跑 canonical 构建，避免下次构建产生无意义 diff。该差异不影响 HTML 功能或 JS 产物。

## 10. 二轮门禁真实输出

| 门禁 | 命令 | 实际结果 |
|---|---|---|
| 全量 pytest | `uv run pytest -q`（UTF-8） | `1746 passed in 54.06s`（exit 0） |
| 新增回归用例 | `uv run pytest -q tests/unit/test_engine_runtime_lifecycle.py -k "normalized_engine_settings or direct_http_transport" -vv` | `2 passed, 11 deselected` |
| Ruff Lint | `uv run ruff check .` | `All checks passed!`（exit 0） |
| Ruff 目标格式 | `uv run ruff format --check`（12 个改动 Python 文件） | `12 files already formatted`（exit 0） |
| Ruff 全仓格式 | `uv run ruff format --check .` | 61 文件待格式化，与本次改动交集 0，维持基线红（延后） |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| Web Lint | `bun run lint` | exit 0，0 warning |
| Web 类型检查 | `bun run type-check` | exit 0 |
| Web 构建 | `bun --bun vite build --outDir <临时目录> --emptyOutDir` | 成功；`SettingsView.js` 183.66 kB；临时产物哈希与仓库静态 JS 一致 |
| Go 构建 | `go build ./...`（CGO/Zig + SpiderMonkey 配方） | exit 0 |
| Go 测试 | `go test ./...` | 全部 `ok`（部分 cached），exit 0 |
| gofmt | `gofmt -l .` | 无输出 |
| Diff 空白 | `git diff --check e2c8843` | exit 0（仅 CRLF 提示，无空白错误） |
| 隐私扫描 | diff + 本报告 | 0 命中 |

## 11. 剩余延后台账

- **Q-1**：visit-id 侧车不可写/rename 失败路径与同账号并发冷启动竞态未覆盖；单进程串行场景可接受。
- **Q-2**：`_direct_http_warned` 无锁；仅可能重复 WARNING，不影响正确性。
- **Q-3**：新增回归用例的 `idle_ttl_s=3600` 与兜底同值，建议改为非默认值以增强判别力；二轮已用 4321 独立验证，不阻断。
- **Q-4**：仓库内未找到独立的“VPS 需同步 3600”部署清单；属外部运维文档，无法独立核验。
- **P2-4**：`static/index.html` 空行/行尾漂移，建议提交前恢复规范构建产物。
- 全仓 `ruff format` 基线红（61 文件）继续按既有约定延后，不计入本批次。

## 12. 二轮结论

- 五项返工均已实质闭环：P1-1 读取侧修复与真实形状回归成立且无扁平/嵌套 Shim；P1-2 根运行态已 3600；P2-1 源码与编译 JS 清理完成；P2-2 仅一行；P2-3 两处兜底已 3600。
- 二轮独立门禁全绿：pytest 1746、ruff/pyright、web lint/type-check/build、Go build/test/gofmt、diff 检查均通过；隐私扫描 0 命中。
- **P0/P1 已清零，最终判定 PASS**。剩余 P2-4（静态 index 空行）与 Q-1..Q-4 均为非阻断延后项；建议提交前顺手清理 P2-4，但不需要再开复审轮次。

> **二轮只读声明**：除本报告追加段落外，未修改任何仓库文件；临时构建目录已删除；未启动服务、未联网、未执行提交或推送。
