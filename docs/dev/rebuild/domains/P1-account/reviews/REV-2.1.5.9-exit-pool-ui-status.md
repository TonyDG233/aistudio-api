# REV-2.1.5.9 — 出口池只读状态卡片与 idle_ttl 对齐复审

- 审查对象：`REV-2.1.5.9`（出口池只读状态卡片 + `/config` 只读字段 + 沙盒假字段 + idle_ttl 前端默认值对齐）
- 审查方式：100% 只读独立复审（源码/测试/文档审阅 + 只读门禁实跑 + 逻辑离线复算）
- 审查时间：2026-11-23
- 工作区基线：`8268f21`；`git status` 显示同工作区还并存 `2.1.5.8`（出口池特殊模式主体）与 verify-account 等步骤的未提交改动，本报告结论仅针对下列审查对象。
- 截图归档：沿用实现阶段已落盘资产（本轮不启动服务/浏览器，仅核对文件存在与尺寸）
  - `docs/dev/rebuild/domains/P1-account/reviews/assets/exit-pool-settings-external-1440x900.png`
  - `docs/dev/rebuild/domains/P1-account/reviews/assets/exit-pool-settings-external-down-1440x900.png`

---

## 一、结论：PASS

- P0：0 项；P1：0 项；P2：5 项（均为可维护性/文案/测试补强，不阻塞合入）。
- 必查项 A–G 全部满足；四项硬门禁（ruff / pyright / pytest / web lint+type-check+bun test）实跑全绿。
- idle_ttl 全栈已对齐 `3600`（Go signer 默认、Python 运行态默认、`defaults/config.yaml`、`defaults/config.schema.json`、前端 schema recommended/default），本步仅修前端 schema 的 900→3600，未改分发默认与 config schema。

---

## 二、问题清单

### P0

无。

### P1

无。

### P2

1. **`/config` 新增只读字段缺少后端回归用例（测试缺口）**
   - 位置：`src/aistudio_api/api/routes_system.py:61-82`、`src/aistudio_api/api/routes_system.py:778-823`
   - 现状：`test_exit_pool.py` 覆盖了 `pool_status` 本身，但没有任何用例断言 `/config` 的三种形态：①env 缺失 → `proxy_mode` 为 `single`/`direct` 且 `exit_pool is null`、不创建 resolver；②external → 返回 `external_pool` + 状态字典；③沙盒 query 只在 `ui_debug` 下生效。
   - 复现：`Select-String tests/unit -Pattern "proxy_mode|exit_pool"` 零命中；`tests/unit/test_ui_debug_sandbox.py:749` 取到 `/config` 但只断言 `yaml_content.engine.mode`。
   - 建议：补 3 条用例（默认/external 假 resolver/沙盒 query 门控），其中默认用例可 monkeypatch `get_exit_resolver` 返回 `None` 并断言未触达网络。

2. **管理器不可达文案与实际回退语义不完全一致**
   - 位置：`web/src/views/SettingsView.vue:495`
   - 现状：文案固定为“管理器不可达，请求已回退全局代理”。实际 `ExitResolver.resolve()` 失败时回退 `settings.proxy_url`（`resolver.py:246-271`）；若未配置全局代理则等价直连；且本卡片只能证明 `/v1/status` 探测失败，并不代表分配路径一定失败。
   - 复现：external 模式 + `manager_ok=0` 沙盒截图即触发该分支；清空 `AISTUDIO_PROXY` 后实际走了直连。
   - 建议：改为“管理器状态不可用，请求按全局代理/直连回退”一类不越权断言语义的表述。

3. **调试用第二次 `/config` 请求绕过 `systemApi` 封装且与首请求重复**
   - 位置：`web/src/views/SettingsView.vue:97-109`
   - 现状：`loadConfig()` 先 `systemApi.getConfig()`，ui-debug 且 URL 带 `proxy_mode` 时再直接 `request<SystemConfig>('/config?...')`，同一页面加载发生两次 GET；API 调用未收敛到 `web/src/api/system.ts`（既有约定）。
   - 复现：ui-debug 下带 `?proxy_mode=external_pool` 打开设置页，网络面板两次 `/config`。
   - 建议：给 `systemApi.getConfig(params?: { proxy_mode?: string; manager_ok?: string })` 增加可选 query，视图只调用一次。

4. **`/v1/status` 契约未在仓库内固化，形状假设偏强**
   - 位置：`src/aistudio_api/infrastructure/exit_pool/resolver.py:123-137`、`resolver.py:301-317`
   - 现状：要求 `payload["exits"]` 必须是 dict 且 `manager_ok is True`，否则整包降级为全零；而 `research/exit-pool/EVALUATION.md:108-112` 只定义了 `/v1/assign`、`/v1/events`，`/v1/status` 是本步新增契约，管理器侧需按 `{manager_ok: true, exits: {healthy,total,quarantined,disabled}, bound_accounts, last_switch_at}` 输出，否则卡片永久“不可达”。
   - 复现：离线复算 `_parse_status({"exits": []})` 与 `_parse_status({"exits": {}}) `→ 前者降级全零（后者 `manager_ok=False` 全零）。
   - 建议：在 `research/exit-pool/` 或 `EVALUATION.md` 补 `/v1/status` 响应样例与字段表，作为宿主管理器的实现依据。

5. **状态缓存为单槽且无并发合流，突发下会重复探测**
   - 位置：`src/aistudio_api/infrastructure/exit_pool/resolver.py:273-299`
   - 现状：15s 缓存命中后才不请求；缓存为空时多个并发 `/config` 会各自发起一次 `GET /v1/status`（随后各自覆盖缓存）。无内存泄漏（单槽 dict、返回值均为副本），但冷缓存突发会产生 N 次 2s 上限的探测。
   - 复现：并发 5 次 `await resolver.pool_status()`（空缓存）→ 管理器侧可见 5 次 `/v1/status`。
   - 建议：如需削峰，可复用 `_fetch_lock` 或在 `pool_status` 内加轻量 in-flight 合并；非必须。

---

## 三、Q / N

- **N-1 命名与调研文档有偏差**：`EVALUATION.md:245` 规划 `proxy_mode: "off"|"single"|"external_pool"`，实现用 `direct`（无全局代理时）替代 `off`，语义更贴合真实回退路径，且与本次必查项“single/direct + null”一致，判定可接受；建议在文档中同步该命名。
- **N-2 `bound_label` → `bound_accounts`**：调研文档规划展示“当前账号绑定出口的脱敏标签”，实现改为展示“绑定账号数”计数，未透出任何出口 id/账号/ socks URL，安全性更好，属有意收敛，非缺陷。
- **N-3 环境代理会影响默认展示**：`proxy_mode` 由 `settings.proxy_url` 推导，而 `settings.proxy_url` 在导入期从 `AISTUDIO_PROXY/HTTP(S)_PROXY` 发现（`config.py:218-227,403`）。开发机带系统代理时，未做任何配置也会显示“已配置代理/single”。这与改造前 `proxy_configured` 的行为一致，非回归。
- **N-4 负结果缓存 15s**：管理器恢复后 UI 最多滞后 15s 才转为在线；属可接受的观测性折衷，建议在运维说明中一句话注明。
- **N-5 Bearer over HTTP**：`/v1/status`、`/v1/assign`、`/v1/events` 均以 `Authorization: Bearer` 访问可作为任意 `http(s)://host` 的 `AISTUDIO_EXIT_ENDPOINT`。默认部署在 loopback/docker 桥，风险可控；若允许远端 endpoint，建议文档明确要求 TLS。
- **N-6 沙盒无 query 时返回 `single` 而非按 `proxy_configured` 返回**：`ui_sandbox.py:109-110` 固定返回 `("single", None)`；沙盒内 `proxy_configured` 可能为 false，标题显示“网络代理状态/直连”属既有沙盒形态，不影响生产。

---

## 四、逐项核对（A–G）

- **A 默认模式**：`_resolve_proxy_status`（`routes_system.py:79-82`）在 `get_exit_resolver()` 为 `None` 时直接返回 `("single" if settings.proxy_url else "direct"), None`，不构造 `httpx.AsyncClient`、不发任何管理器请求；`get_exit_resolver()` 仅在 `AISTUDIO_EXIT_MODE=external` 且 endpoint/token/hmac 齐备时创建（`config.py:96-152`）。旧字段 `proxy_configured` 保留（`routes_system.py:811`），旧前端不受影响；新前端对 `proxy_mode`/`exit_pool` 均可选并带 fallback（`types/system.ts:18-19`、`SettingsView.vue:70-74`）。离线复算：清空 `AISTUDIO_EXIT_*`/`HTTP(S)_PROXY` 后 `get_exit_resolver() is None`、`prod_mode=direct`。**通过**。
- **B external 模式**：`pool_status` 仅在 resolver 存在时被调用（全仓仅 `routes_system.py:82` 一处生产调用）；`asyncio.wait_for(..., timeout=2.0)`（`resolver.py:286-288`）+ 模块常量 `STATUS_TIMEOUT_S=2.0`；15s 缓存（`resolver.py:281-284`，边界用例覆盖 14.9 命中/15.0 重取）；失败统一 `_status_failure()` 全零；白名单只取 7 个字段，`secret`/`version` 被丢弃（`resolver.py:129-137`，测试 `test_exit_pool.py:501-511`）；不返回 socks_url/token/exit_id/账号。缓存为单槽、返回值 `dict(...)` 副本，无泄漏；并发冷启动仅重复探测，见 P2-5。**通过**。
- **C 安全**：调试 query 仅在 `runtime_state.ui_debug and runtime_state.ui_sandbox is not None` 分支读取（`routes_system.py:72-75`），生产路径完全忽略同名 query；`ui_debug` 只由 `AISTUDIO_UI_DEBUG`/`diagnostics.ui_debug` 决定（`config.py:311-329`），无 query 注入面；沙盒假字段不含 socks/token/出口 id（`ui_sandbox.py:90-114`）；卡片仅展示计数与时间。**通过**。
- **D 前端三分支**：external → 只读池卡片；非 external → 沿用原卡片并按 `proxy_configured` 区分“已配置代理/直连”，即 external/single/direct 三态正确；`manager_ok=false` 有独立文案（`SettingsView.vue:471-496`）；`last_switch_at` 用 `<=0/非数值 → 尚无切换记录`、否则 `new Date(ts*1000).toLocaleString('zh-CN',{hour12:false})` 本地时间（`SettingsView.vue:77-82`），0/非法值处理后端 `_as_int` 已兜底为 0；无新增控件/设置项。**通过**（文案精度见 P2-2）。
- **E 测试质量**：`pool_status` 覆盖成功+白名单+Bearer（`test_exit_pool.py:471-514`）、15s 缓存边界（`:517-543`）、503/非法 JSON（`:546-562`）、httpx 传输超时（`:565-575`）、`wait_for` 超时（`:578-592`）；`systemApi` 用例断言 `proxy_mode`/`exit_pool` 各字段透传（`web/tests/systemApi.test.ts:54-91`），字段白名单本身由后端精确等值断言把关。缺口见 P2-1。
- **F idle_ttl 对齐**：`web/src/components/config/schema.ts:471-481` 仅改 3 行（explanation/defaultValue/recommendedValue）900→3600，未动 min/max 等无关字段；`defaults/config.yaml:39`、`defaults/config.schema.json:113-117`（`minimum:1, default:3600`）、`model_defaults.py:746-748`、`engine_runtime.py:209`、`local_daemon.py:60`、`waa-signer/cmd/waa-signer/main.go:48` 全部为 3600；`web/tests/configTooltip.test.ts:122` 用 `defaults/config.yaml` 反查 recommended，可防再次漂移。`git diff -- defaults/` 为空（分发配置本步未改）。**通过**。
- **G 回归/产物一致性**：`git diff` 未触及 `defaults/config.schema.json` 语义、未新增 `config.yaml` 键、仅新增 GET 字段（无 PUT/UI 设置项）；只读核对 `src/aistudio_api/static/assets/SettingsView.js` 已含 `external_pool`/`外部出口池`/`管理器不可达`/`尚无切换记录`/`health` 计数与 `idle_ttl_s ... 3600`，且该产物 mtime（03:21:42）晚于源文件 `SettingsView.vue`（02:39:39），判定产物已同步（未执行 build）；`static/index.html` 的 diff 仅为行尾/BOM 归一，无内容语义变化。截图 3 组（external/default/external-down × 1440/1024/768）存在且尺寸与命名一致：`exit-pool-settings-external-1440x900.png`、`exit-pool-settings-external-down-1440x900.png` 等。**通过**。

---

## 五、实跑门禁输出（本机 Windows / 只读复核）

| 门禁 | 命令 | 结果 |
|---|---|---|
| Ruff | `uv run ruff check .` | `All checks passed!`（EXIT=0） |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| Pytest（定向） | `uv run pytest -q tests/unit/test_exit_pool.py tests/unit/test_ui_debug_sandbox.py` | `61 passed in 8.80s` |
| Pytest（全量） | `uv run pytest -q` | `1847 passed in 53.35s`（EXIT=0） |
| Web 测试 | `bun test`（web/） | `102 pass / 0 fail / 843 expect() / 11 files, 129.00ms`（EXIT=0） |
| Web lint | `bun run lint`（web/） | EXIT=0 |
| Web 类型 | `bun run type-check`（web/） | `vue-tsc -b`，EXIT=0 |

补充只读核对：
- 逻辑复算（`uv run python -c`，子进程清空 `AISTUDIO_EXIT_*` 与 `HTTP(S)_PROXY`，无网络）：`get_exit_resolver() is None`、`prod_mode=direct`、`sandbox_proxy_status(None)=('single', None)`、`sandbox_proxy_status('external_pool')=(external_pool, 3/4 计数)`、`sandbox_proxy_status('external_pool','0').manager_ok=False`、`_parse_status({'exits': []})` 降级全零。
- 静态产物只读 grep：`SettingsView.js` 命中 `外部出口池`×1、`管理器不可达`×2、`proxy_mode`×3、`last_switch_at`×1；`idle_ttl_s` schema 块为 `defaultValue:3600, recommendedValue:3600`。

**未执行 `bun run build` 的原因**：本轮是只读复审，build 会改写 `src/aistudio_api/static/` 产物，违反只读约束；改用“产物字符串/数值存在性 + 产物 mtime 晚于源文件”的只读方式核对一致性，并在上表标注该限制。

---

## 六、只读声明

- 本次复审未修改任何源码、测试、配置、`defaults/`、`static/` 产物或 git 索引；未执行 `git add/commit`；未启动服务、浏览器、Chromium、WARP 隧道或任何网络请求；未访问 VPS、外网或真实账号凭据。
- 唯一写操作为本文件落盘：`docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.9-exit-pool-ui-status.md`。
- 所有门禁均为本地只读命令实跑；`uv run python -c` 仅做内存逻辑复算，已清空相关环境变量确保不发网络请求。

---

## 七、二轮复审（2026-11-23，5 个 P2 返工复核）

### 7.1 二轮结论：PASS

- 上一轮 5 个 P2 全部落实且断言真实；**残留 P0/P1/P2 = 0**。
- 门禁全绿（含新增用例），可直接进入下一阶段。

### 7.2 逐条复核

1. **`/config` 后端用例（P2-1）— 已闭环**
   - `tests/unit/test_routes_system.py:504-629` 新增 6 个真实用例并实跑通过：
     - `test_get_config_without_external_mode_skips_manager[None-direct]` / `[socks5://127.0.0.1:1080-single]`：断言 `proxy_mode` 精确值、`exit_pool is None`，并把 `resolver_module.ExitResolver` 替换为会抛错的桩，断言 `builds == []`（即 env 关闭时绝不构造 resolver，非“只看 200”）。
     - `test_get_config_external_pool_returns_whitelisted_status`：断言请求路径 `/v1/status`、`Authorization: Bearer secret-token`、`exit_pool` 与 7 字段白名单**整体等值**（含丢弃未知字段）。
     - `test_get_config_external_manager_down_reports_manager_ok_false[http_503|timeout]`：503 与 `httpx.ReadTimeout` 两条路径均断言 `exit_pool == _EXIT_POOL_DOWN`（manager_ok=false 全零）。
     - `test_get_config_ignores_sandbox_query_outside_ui_debug`：`ui_debug=False` 下带 `?proxy_mode=external_pool&manager_ok=1` 仍返回 `direct` + `null`，证明生产忽略沙盒 query。
   - 环境隔离由 `tests/conftest.py:19-28` 的 autouse fixture 清空 `AISTUDIO_EXIT_*` 并 `reset_exit_resolver()`，用例不依赖宿主 env。**P2-1 关闭**。
2. **文案（P2-2）— 已闭环**
   - `web/src/views/SettingsView.vue:497` 现为「管理器状态不可用，请求按全局代理/直连回退」，不再断言“已回退全局代理”。**P2-2 关闭**。
3. **`systemApi.getConfig(params?)` 收敛（P2-3）— 已闭环**
   - `web/src/api/system.ts:20-23,68-79` 新增 `SystemConfigQuery`，按 `proxy_mode` → `manager_ok` 顺序 `URLSearchParams` 拼装；`SettingsView.vue:93-111` 只调用一次 `systemApi.getConfig(sandboxProxyQuery())`，原 `request<SystemConfig>` 直连已删除（无重复 GET）。
   - `web/tests/systemApi.test.ts:140-171` 断言三条 URL 精确顺序：`/config?proxy_mode=external_pool&manager_ok=0`、`/config?proxy_mode=external_pool`、`/config`。**P2-3 关闭**。
4. **`/v1/status` 文档契约（P2-4）— 已闭环**
   - `docs/dev/rebuild/domains/P1-account/research/exit-pool/CONTRACT.md:32-96` 与 `data/tools/exit-manager/DEPLOY.md:274-286` 字段一致：`manager_ok`、`exits.{total,healthy,quarantined,disabled}`、`bound_accounts`、`last_switch_at`（Unix 秒，从未切换为 0）；鉴权一致（`Authorization: Bearer <token>` + `security.allowed_client_cidrs`，只接受 GET）；失败语义一致（app 侧降级不抛错，`STATUS_TIMEOUT_S=2.0`、15s 负缓存、冷缓存合流；CONTRACT §2.3 为 DEPLOY §5.4 的 app 侧细化，无矛盾）。
   - 另交叉核对宿主实现 `data/tools/exit-manager/exit_manager/manager.py:758-773` 与 `exit_manager/server.py:144-167`：`status_summary()` 输出与契约逐字段一致，`_authorized()` 用常量时间比较 Bearer（`server.py:82-89`），`/v1/status` 仅 GET。**P2-4 关闭**。
5. **冷缓存合流（P2-5）— 已闭环**
   - `src/aistudio_api/infrastructure/exit_pool/resolver.py:159,274-309` 新增 `_status_lock`：快速路径先查缓存，锁内二次查缓存，未命中才 `await asyncio.wait_for(_fetch_status(), timeout=STATUS_TIMEOUT_S)`；超时/异常仍在锁内降级并写入缓存。
   - 无死锁：全类只有 `_fetch_lock` 与 `_status_lock`，无嵌套获取（`resolve` 用前者、`pool_status` 用后者），锁持有上限为 `wait_for` 的 2s，`CancelledError` 原样抛出且 `async with` 保证释放。
   - `tests/unit/test_exit_pool.py:749-773` 用 `asyncio.gather` 并发两次冷缓存调用，断言 `calls == 1` 且结果一致；`test_pool_status_wait_for_timeout_reports_manager_down` 仍通过，证明超时在加锁后依旧生效。**P2-5 关闭**。

### 7.3 二轮实跑门禁（只读，未跑 `bun run build`）

| 门禁 | 命令 | 结果 |
|---|---|---|
| Ruff | `uv run ruff check .` | `All checks passed!`（EXIT=0） |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| Pytest（定向三文件） | `uv run pytest -q tests/unit/test_routes_system.py tests/unit/test_exit_pool.py tests/unit/test_ui_debug_sandbox.py` | `88 passed in 9.41s` |
| Pytest（新增 /config 用例） | `uv run pytest tests/unit/test_routes_system.py -k get_config -v` | `7 passed, 18 deselected in 1.09s`（含上述 6 个新用例） |
| Pytest（status 用例） | `uv run pytest tests/unit/test_exit_pool.py -k pool_status -v` | `7 passed, 37 deselected in 0.19s`（含并发合流用例） |
| Pytest（全量） | `uv run pytest -q` | `1868 passed in 50.48s`（EXIT=0；较上轮 1847 增加 21） |
| Web 测试 | `bun test`（web/） | `103 pass / 0 fail / 844 expect() / 11 files, 146.00ms`（EXIT=0） |
| Web lint | `bun run lint`（web/） | EXIT=0 |
| Web 类型 | `bun run type-check`（web/） | `vue-tsc -b`，EXIT=0 |

只读产物与截图核对：
- 未执行 `bun run build`（会改写 `src/aistudio_api/static/`，违反只读）。只读 grep：`src/aistudio_api/static/assets/SettingsView.js` 命中新文案「管理器状态不可用，请求按全局代理/直连回退」×1、「外部出口池」×1；产物 mtime `04:08:31` 晚于 `SettingsView.vue` `04:06:23`，判定产物已同步。
- 设置页截图共 9 张（含复审要求的 6 张 external/down）：`exit-pool-settings-external-{1440,1024,768}x900.png`、`exit-pool-settings-external-down-{1440,1024,768}x900.png`，另有 default 3 张；逐张用 `System.Drawing` 读尺寸，全部与文件名一致（1440x900 / 1024x900 / 768x900）。

### 7.4 二轮 N（不计缺陷）

- **N-7 前端在首请求即转发沙盒 query**：`SettingsView.vue:93-111` 不再先判 `cfg.ui_debug`，生产 URL 若带 `proxy_mode` 也会发给后端；后端生产路径明确忽略（`test_get_config_ignores_sandbox_query_outside_ui_debug` 已证），无功能/安全影响。
- **N-8 沙盒内 API Key 变更后的刷新未带 query**：`SettingsView.vue:163` 的 `handleApiKeysChanged()` 仍用无参 `getConfig()`，ui-debug 下新增/删除 API Key 后外部池卡片会回到沙盒默认 `single`；仅影响沙盒观感，生产无影响。可选在该处复用 `sandboxProxyQuery()`。
- **N-9 文档详略不对称**：`DEPLOY.md §5.4` 未重复 app 侧 2s/15s 负缓存/合流细节，`CONTRACT.md §2.3` 作为 app 侧细化补充，两者无冲突；建议后续把 app 侧一段同步进 DEPLOY，保持“镜像”名副其实。

### 7.5 二轮只读声明

- 本轮仍 100% 只读：未修改源码/测试/配置/`defaults/`/`static/`/git 索引；未 `git add/commit`；未启动服务、浏览器或任何网络请求；未访问 VPS/外网/真实凭据。
- 唯一写操作：向本报告追加本章节。
- 二轮实跑均为本地只读命令；bun build 未执行，产物一致性以字符串存在性 + mtime 关系核对。
- 结论：**残留 P0/P1/P2 = 0，可进入下一阶段。**
