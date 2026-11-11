# REV-2.1.2 — Ultra 提权 / 实例换绑 tier 归一化 / Browserless 403 主动续期与 Go 热 Worker 重建（终审）

- **审查对象**：`2.1.2` 全部工作区改动（3 项修复 + `verify-account` 移动设备验证自动熔断隔离 + 配套测试 / 前端产物 / changelog）
- **工作区**：`D:\LLM\self_programming\aistudio-api`
- **基线**：`HEAD = 390d202`（改动均为未提交工作区增量）
- **审查方式**：独立静态代码审查 + `--ui-debug` 离线沙盒实测 + 全量门禁实跑。
- **复审轮次**：`REV-2.1.2-R2`（2026-11-11），针对 `P2-1` / `P2-2` / `Q-1` / `Q-2` 四项修复的独立复验，见第五节。
- **R3 复审**：`REV-2.1.2-R3`（2026-11-11），针对 `verify-account` 移动设备验证风控自动熔断隔离的独立审查，见第七节。
- **只读声明**：除以下三类操作外，未修改任何业务代码、配置、测试或文档：
  1. 按任务要求对 12 个已改动 Python 文件执行 `uv run ruff format`（其中 6 个仅格式化，无语义变化；已用 `git diff -w` 复核）；
  2. 归档截图 `docs/dev/rebuild/domains/P8-api-ui/reviews/assets/c212-reassign-pro-modal-1440.png`；
  3. 写入本报告 `REV-2.1.2.md`。
  R2 复审只更新本报告，未触碰任何代码、测试或配置。
  未执行 `git commit` / `git push` / `git tag`，未启动真实生产服务或真实浏览器（沙盒进程已停止，端口 18091 已关闭）。

---

## 结论

**PASS（0 项 P0 / 0 项 P1 / 0 项 P2 / 0 项 Q / 7 项 N）。**

三项修复的主链逻辑、优先级顺序、归一化闭环与 Go/Python 协同均与任务目标一致；全量 Python 测试 1452 项、前端 94 项测试、lint、type-check 全绿；`--ui-debug` 沙盒在 `1440x900` 下实测通过并成功留档。R2 复审（第五节）实跑 Go 单测与 Python 门禁，确认原 `P2-1` / `P2-2` / `Q-1` / `Q-2` 四项全部修复关闭。

- **P0**：0（无阻断性缺陷）
- **P1**：0（无主链逻辑缺陷）
- **P2**：0（原 2 项：Go 热 Worker 差异判定方向性、`entry.mu` 内查询 Cookie 的锁粒度 —— 均已在 R2 复审确认修复关闭）
- **Q**：0（原 2 项：新增 Go 辅助函数缺少单测、`bool` 被 `isinstance(..., int)` 当作整数位 —— 均已在 R2 复审确认修复关闭）
- **N**：7（沙盒种子与任务描述的实例编号差异、Windows 本机 `go build` 平台约束、`index.html` 仅 stat 变更、格式化范围、静态产物一致性、截图体积，以及 R2 新增的热 Worker 基线残余观察）

---

## 一、门禁实跑输出（Reviewer 亲自执行）

| 门禁命令 | 实际输出 | 结果 |
|---|---|---|
| `uv run ruff check .` | `All checks passed!` | **PASS** (exit 0) |
| `uv run ruff format --check <12 个改动文件>` | `12 files already formatted` | **PASS** (exit 0) |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` | **PASS** (exit 0) |
| `$env:PYTHONUTF8='1'; uv run pytest -q` | `1452 passed in 48.84s` | **PASS** (exit 0) |
| `cd web && bun run lint` | `eslint src --max-warnings 0`，0 error / 0 warning | **PASS** (exit 0) |
| `cd web && bun run type-check` | `vue-tsc -b`，0 error | **PASS** (exit 0) |
| `cd web && bun test` | `94 pass, 0 fail, 771 expect() calls, 11 files` | **PASS** (exit 0) |
| `cd web && bun test tests/instanceTier.test.ts` | `4 pass, 0 fail, 20 expect() calls` | **PASS** (exit 0) |
| `git diff --check` | 无输出，`exit 0` | **PASS** |
| `git ls-files config.yaml` | 空输出（未跟踪，符合口径） | **PASS** |
| `gofmt -l waa-signer/cmd/waa-signer/main.go` | 空输出 | **PASS** |
| `go build ./...`（waa-signer） | `build constraints exclude all Go files in .../internal/waa/mozjs` | **平台限制**（见 N-2，非本次改动回归） |

---

## 二、`--ui-debug` 沙盒实测

- **启动命令**：`uv run python main.py server --port 18091 --ui-debug`（`background_process` 启动，端口就绪后操作；结束时 `stop`）。
- **视口**：`1440x900`。
- **路径**：`http://127.0.0.1:18091/accounts`（前端为 history 路由；`#/accounts` 会停在控制面板）。
- **种子差异说明**：默认 `all_states` 场景的真实付费区间槽位是 **实例 #1**（`min_tier=Pro`、`max_tier=Ultra_20X`），实例 #2 为 Free-only。为严格对位任务要求的“实例 #2 / `Pro 至 Ultra_20X`”，实测切换到 UI Debug 场景 **窄屏边界（`narrow_edge`）**：该场景 `inst-2` 正是复现用例（`min_tier="Pro"`、未配置 `max_tier`）。随后在沙盒内启用了 `Ultra 20X Disabled Edge` 账号（仅内存沙盒状态），使换绑下拉框具备兼容的 Pro 及以上候选。
- **实测步骤与观察**：
  1. 实例池渲染 `#2` 槽位摘要为 **`Pro 至 Ultra_20X`**（不再出现 `仅限 Pro`）；
  2. 点击 `#2` 的「绑定账号 / 换绑」入口打开 `ReassignAccountModal`，标题为 **`实例 #2 换绑账号`**；
  3. 弹窗槽位行显示 **`对话与工具 (Chat) · 槽位：Pro 至 Ultra_20X`**；
  4. 下拉框存在兼容候选并通过 `selectOption` 选中 `ultra20x.edge@example.com · Ultra 20X 会员`，出现确认提示 `将把实例 #2 绑定到 ultra20x.edge@example.com`；
  5. 未点击「确认换绑」（不触发任何写操作），直接取消关闭。
- **截图**：`docs/dev/rebuild/domains/P8-api-ui/reviews/assets/c212-reassign-pro-modal-1440.png`（186,491 bytes；已人工复核画面包含上述标题、槽位文案与候选账号）。
- **收尾**：`background_process stop` 成功，`Test-NetConnection 127.0.0.1:18091` 返回 `False`（仅有 TIME_WAIT 残留，无监听进程）。

---

## 三、逐项审计清单核验

### 1. 修复项 1：`item[82]` `accessModes` Ultra 提权与 `gemini-4-carbon` 兜底

**核验目标**：`4 in modes and 3 not in modes` 必须位于静态 `paid_model_rules` 之前，避免宽泛的 `gemini-4` 子串规则把 Ultra 独占模型降级为 Pro。

**代码证据**：
- `src/aistudio_api/application/routing_eligibility.py:142-149`：新增 `_access_modes()`，统一从缓存条目提取整数 `accessModes`。
- `routing_eligibility.py:173-182`：保留 live Free 最高优先级（`live_truth is False -> ()`）；随后 `info = model_discovery.get_cached_model_info(clean)`，在 `if 4 in modes and 3 not in modes: return ("Ultra", "Ultra_20X")`（181-182）。
- `routing_eligibility.py:184-201`：静态 `paid_model_rules` 匹配被放在 Ultra-only 判定之后。
- `src/aistudio_api/infrastructure/gateway/model_discovery.py:976-990`：`truth is False` 先返回 `[]`；983-990 行在读取规则前完成 Ultra-only 判定并返回 `["Ultra", "Ultra_20X"]`；`992-1022` 才进入 `paid_model_rules` 循环。
- 优先级在两条实现链上完全一致：Live Free → Live/cached Ultra-only → 静态规则 → `accessModes 3` / `paid` / capability。

**配置一致性**：
- `defaults/config.yaml:190`：`ultra_required.contains` 已加入 `carbon`。
- `web/src/components/config/schema.ts:828`：`recommendedValue.ultra_required.contains` 同步加入 `'carbon'`；`844`、`854` 的解释文案同步为 `veo/ultra/carbon`。
- `src/aistudio_api/domain/model_access.py:23`：`_DEFAULT_PAID_MODELS` 加入 `gemini-4-carbon`。
- `model_discovery.py:323-330`：`_FALLBACK_MODELS` 加入 `gemini-4-carbon`（`paid: True`、`access_tier: 2`、`accessModes: [4]`）；`access_tier == 2` 与 `1283` 行的 Paid 判定语义一致。
- 构建产物 `src/aistudio_api/static/assets/SettingsView.js` 已包含 `carbon`（静态产物与源码同步）。

**测试证据**：
- `tests/unit/test_model_tier_admission.py:91-113`：缓存 `[4]` 的模型即使命中 `gemini-4` Pro 规则也返回 Ultra，且 `model_discovery.get_required_tiers` 同步。
- `tests/unit/test_model_tier_admission.py:116-129`：读取 tracked `defaults/config.yaml`，断言 `carbon` 在 `ultra_required` 且解析为 Ultra。
- `tests/unit/test_model_tier_admission.py:132-145`：`accessModes [3]` / `[3,4]` 仍保持 Pro 及以上（Ultra-only 提前返回未误伤）。
- `tests/unit/test_routing_eligibility.py:158-182`：Pro-only 池请求 `gemini-4-carbon` 抛 `TierRequiredError`（403，`required_tier=Ultra`、`current_tier=Pro`、未进入 `begin`）。
- `tests/unit/test_routing_eligibility.py:116-133`：非 Ultra 的 `accessModes [3]` 仍让静态规则优先，证明提前返回只针对 Ultra-only。

**判定**：**PASS**。

---

### 2. 修复项 2：实例槽位 `max_tier` 归一化与换绑弹窗兼容过滤

**核验目标**：`min_tier: Pro`（未显式 `max_tier`）的槽位必须在后端 API 即归一为 `Pro..Ultra_20X`，前端在 `max_tier` 为空时也要给出同样的兼容区间，换绑弹窗不得再显示“仅限 Pro”或过滤掉 Pro/Ultra 账号。

**代码证据**：
- `src/aistudio_api/infrastructure/gateway/instance_pool.py:78-103`（`InstanceSpec.from_mapping`）：`selector = resolve_tier_selector(...)` 后，非 `only_tier` 分支返回 `max_tier=selector.max_tier`（102），不再写回原始空串；`selector` 对 `min_tier` 单独配置的付费槽位展开为 `Pro..Ultra_20X`（`domain/model_access.py:212-215`）。
- `src/aistudio_api/application/instance_manager.py:1400-1417`（`_view`）：统一取 `selector = record.spec.selector`，序列化 `min_tier` / `only_tier` / `max_tier` 全部来自归一后的选择器（1410-1412）。
- `web/src/utils/instances.ts:56-73`（`instanceTierRange`）：`max_tier` 为空时，Free 保持 `Free..Free`，付费回退 `Ultra_20X`。
- `web/src/utils/instances.ts:84-96`（`slotSummary`）：同口径文案回退，`min_tier=Pro` 输出 `Pro 至 Ultra_20X`。
- `web/src/components/instances/ReassignAccountModal.vue:35-45`：候选过滤使用 `isTierCompatible`；`:87` 使用 `slotSummary` 展示槽位；`:112` 展示账号等级。
- `web/src/components/accounts/InstancePoolCard.vue:239-241, 268-271`：已绑定槽位为「换绑」、未绑定槽位为「绑定账号」，两者均打开同一 `ReassignAccountModal`（因此沙盒 `narrow_edge` 的未绑定 #2 也能实测同一弹窗标题）。

**闭环点**：
- 后端 `from_mapping`（配置装载）与 `_view`（API 序列化）均归一，避免“配置正确但 API 仍返回空 `max_tier`”；
- 前端仍保留空串回退，兼容旧缓存/旧响应；
- `only_tier` 路径不受影响（`Ultra -> Ultra..Ultra_20X`、`Free -> Free..Free`、`Pro -> Pro..Pro`），`instanceTierRange` 的 `only` 分支优先。

**测试证据**：
- `tests/unit/test_instance_pool.py:400-411`：`from_mapping({min_tier: Pro})` 断言 `max_tier=Ultra_20X` 且 `accepts_tier` Pro/Ultra 为真、Free 为假。
- `tests/unit/test_instance_pool.py:413-424`：`min_tier=Free` 仍保持 `Free..Free`。
- `tests/unit/test_instance_manager.py:802-823`：直接构造与 `from_mapping` 两种 spec 的 API 均序列化为 `only="" / min=Pro / max=Ultra_20X`。
- `tests/unit/test_instance_manager.py:825-843`：`only_tier=Free/Pro` 的 API 输出分别收敛为 `Free..Free`、`Pro..Pro`。
- `web/tests/instanceTier.test.ts`：对 `max_tier=""` 与 `"Ultra_20X"` 两种输入断言 `{min:2,max:4}`、Pro/Ultra 兼容、Free 不兼容、摘要为 `Pro 至 Ultra_20X` 且不是 `仅限 Pro`；Free 槽位与 `only_tier=Pro` 亦覆盖。

**判定**：**PASS**（沙盒截图见第二节）。

---

### 3. 修复项 3：Browserless 403 `refresh_auth_state` 主动 `/RotateCookies` 与 Go 热 Worker 更新/释放

**核验目标**：同账号 403 刷新必须先持久化内存中已收到的脏 Cookie，再调用 `/RotateCookies` 并落盘，随后释放 Go 侧热 Worker，最后用最新凭据 `mint_proof`；Go 侧热复用路径能识别滚动凭据差异并按最新 storage state 重建。

**Python 侧证据**（`src/aistudio_api/infrastructure/waa/browserless_session.py`）：
- `:523-524`：`if self._cookie_dirty: await self.save_current_cookies()` —— 先保存脏 Cookie，避免 `_load_storage_state()` 回滚。
- `:525-527`：清空 `_mint_updates` / `_rpc_set_cookies` / `_rpc_account_key`。
- `:528-529`：重新读盘后调用 `await self._rotate_cookies()`。
- `:530-534`：旋转后仍无 `SAPISID` 则返回 False（不会带着死会话 mint）。
- `:537-538`：`release_worker(account_key=self.stable_account_key)` 强制丢弃热 Worker。
- `:539-549`：再以 `self._cookie_text()` 调用 `mint_proof`。
- `:565-601`（`_rotate_cookies`）：`rotate_google_cookies(cookie_map)` 原地更新白名单滚动凭据；合并 `set_cookies`（字符串头走 `apply_set_cookies`，字典项直接写回）；仅 `ok` 时生成 diff、合并、置脏并 `save_current_cookies()`。
- `:184-214`（`_rotation_updates`）：按 name 对比实时 jar，保留原 cookie 元数据（domain/path/expires），新 cookie 以 `.google.com` / `/` / `-1` 落盘。
- 顺序与要求一致：**保存脏 Cookie → `/RotateCookies` 落盘 → `release_worker` → `mint_proof`**。

**Go 侧证据**（`waa-signer/cmd/waa-signer/main.go`）：
- `:432-440`：`rotatingCredentialNames` 覆盖 `__Secure-1PSIDTS`、`__Secure-3PSIDTS`、`SIDCC`、`__Secure-1PSIDCC`、`__Secure-3PSIDCC`。
- `:442-466`：`hotWorkerCookiesStale` 读取 worker 当前 `BrowserStorageState`，仅对比上述滚动凭据；查询失败保守返回 `false`（沿用既有重建/重试路径）。
- `:530-543`：热复用分支在 `Phase == Ready/Busy` 时先做 stale 检测；stale 时不返回热 Worker，而是落入下方 `:546-561` 的既有 `DecideForceRecreate(true, inFlight)` 流程（空闲立即关闭；有在途则 `markRecreatePending` + 有界 `waitForDrain` 后关闭重建），`:563-625` 用最新 `state` 写 `storage-state.json` 并冷建 Worker。
- `:236-240`（`Release`）：有 pin/inFlight/building 时 `skipped`，说明 Python 侧 `release_worker` 在并发在途时可能跳过；此时下一请求的 stale 检测即是实际兜底，链路闭合。

**测试证据**：
- `tests/unit/test_browserless_session.py:17-26`：autouse fixture 将 `rotate_google_cookies` 替换为离线桩，禁止测试触网。
- `tests/unit/test_browserless_session.py:284-296`：刷新前 RPC 已收到的轮换 Cookie 在磁盘重载后仍保留。
- `tests/unit/test_browserless_session.py:298-323`：断言旋转入参含 `SAPISID`、落盘 `__Secure-1PSIDTS=fresh-ts`、事件顺序 `["release", "mint"]`、mint 使用最新 Cookie。
- `tests/unit/test_browserless_auth_rebind.py:46-55`：403 同号刷新测试全局离线；`:510`、`:565` 将期望 release 序列更新为 `[_OLD, _OLD]`（刷新释放一次 + 换绑关闭一次），与实现一致。

**判定**：**PASS**（附 P2/Q 加固建议）。

---

## 四、问题清单

### P2

1. **Go `hotWorkerCookiesStale` 的差异判定是双向的，与注释“请求携带的凭据是否比热 Worker 更新”不完全一致。**
   - 证据：`waa-signer/cmd/waa-signer/main.go:442-447` 注释声明“请求…比热 Worker 内的更新”；`:457-463` 只要值不相等即返回 `true`，未区分谁更新。
   - 风险：同账号并发请求时，某个请求可能携带较旧的滚动凭据快照（另一请求的响应尚未合并），此时会以旧状态重建 Worker，造成凭据短暂回退或不必要的重建抖动；后续 403 刷新可自愈，不影响最终正确性。
   - 建议：仅在 403 刷新路径（或显式 `forceRecreate`）启用该比较，或为滚动凭据引入版本/时间戳，保证“请求更新才重建”。
   - **R2 复审：已修复关闭。** 现实现不再对比 Worker 运行态：`workerEntry.clientRotatingBaseline`（`main.go:68-71`）在冷建时由请求 Cookie 快照写入（`:599`），`closeLocked` 时清空（`:89`）；`AcquireWorker` 热复用分支在 `:518-527` 仅用 `poolstate.HasClientRotatedCredentials(baseline, incoming)` 做纯内存比较，不再调用 `BrowserStorageState`。并发请求携带同一份冷启动基线不会误判；回归用例见 `state_test.go:159-235`（8 组）。

2. **`hotWorkerCookiesStale` 在持有 `entry.mu` 时调用 `BrowserStorageState`。**
   - 证据：`main.go:523` 取 `entry.mu`，`:534` 在锁内调用 `:448-449` 的 `BrowserStorageState`。
   - 现状评估：当前 `goWAARuntime.StorageCookies` 仅做 Cookie 快照序列化（`waa-signer/internal/aistudio/runtime_go.go:805-810`），耗时很低，不构成死锁；但该调用位于每账号串行临界区，若未来 runtime 改为阻塞式查询，会放大同账号获取延迟。
   - 建议：在锁外读取 Cookie 快照，或在注释中明确该调用必须保持非阻塞。
   - **R2 复审：已修复关闭。** `AcquireWorker` 持有 `entry.mu` 的区间（`main.go:507-607`）内已无 `BrowserStorageState` 调用；仅保留 `entry.worker.State()`（`:515`），其实现为 `stateMu.RLock` 读快照（`internal/aistudio/runtime_native.go:179-183`），非阻塞。Cookie 快照改由请求侧 `clientCookieValues`（`:441-450`）在锁内纯内存计算。

### Q

1. **新增 Go 辅助函数没有单元测试。** `waa-signer/cmd/waa-signer` 包当前无 `_test.go`（测试集中在 `internal/*`）。建议把“状态 A vs 状态 B 是否 stale”抽成纯函数并补方向性用例（请求更新 / Worker 更新 / 缺失凭据 / 查询失败）。
   - **R2 复审：已修复关闭。** 新增 `poolstate/state_test.go:108-235`：`TestExtractRotatingCredentials`（5 组）+ `TestHasClientRotatedCredentials`（8 组），合计 13 组新用例，覆盖客户端未变、轮换 `__Secure-1PSIDTS`、仅轮换 `SIDCC`、缺失键、空值键、空基线、无滚动键基线、`nil incoming`。
2. **`bool` 会被 `isinstance(mode, int)` 当作整数位。** `src/aistudio_api/application/routing_eligibility.py:149` 与 `model_discovery.py:985` 均沿用既有写法；`True/False` 会分别变成 `1/0`。对 3/4 判定无实际影响，但建议改为 `type(mode) is int` 以彻底收紧。
   - **R2 复审：已修复关闭。** 两处均改为 `isinstance(mode, int) and not isinstance(mode, bool)`（`routing_eligibility.py:142-157` 的 `_access_modes()`、`model_discovery.py:983-991`）；回归 `tests/unit/test_model_tier_admission.py:148-169` 断言混合 `[True, False, 4]` 仍为 Ultra、纯 `[True, False]` 不产生 Pro 位。附一致性残余：`discovery_entry_paid_truth()`（`model_discovery.py:600-605`）仍沿用旧写法，但其只判定 `3/4` 位，`bool -> 1/0` 不影响结果，属无行为影响的风格残余。

### N

1. **沙盒种子与任务描述的实例编号存在差异。** 默认 `all_states` 的付费区间槽位是 #1（#2 为 Free-only）；本次按任务要求的“实例 #2 / `Pro 至 Ultra_20X`”改用 `narrow_edge`（`inst-2` 恰为 `min_tier=Pro` 无 `max_tier`），并仅在内存沙盒启用一个 Ultra 20X 账号以展示兼容候选。未触碰真实配置/账号。
2. **Windows 本机 `go build ./...` 受 `internal/waa/mozjs` 构建约束限制而失败**（`build constraints exclude all Go files`）。该文件本次未改动，属既有平台差异；Windows 构建应走 `waa-signer/build.ps1`（Zig/CGO），Linux/Docker 走 Dockerfile。`gofmt -l` 为空。
3. **`src/aistudio_api/static/index.html` 在 `git status` 显示 `M`，但 `git diff`/`--numstat` 均为空**，判定为构建后 stat 缓存刷新，无实际内容变更。
4. **`uv run ruff format` 对 12 个目标文件中的 6 个做了格式化**（仅换行/折行，无逻辑改动）；复核 `git diff -w` 后确认所有语义 hunk 均属本次修复。
5. **静态产物与前端源码一致**：`SettingsView.js` 已含 `carbon` 推荐值；`UiDebugMenu.vue_...js` 内联的 `instanceTierRange/slotSummary` 已包含空 `max_tier` 的 `Ultra_20X` 回退。
6. **截图体积 186 KB**，`1440x900` CSS 像素，画面完整包含弹窗标题、槽位文案、候选项与确认提示。
7. **R2 残余观察（非阻断，不重开 `P2-1`）：客户端会把 Worker 回传的滚动凭据原样回送，因此在 Worker 自身诱发轮换时，“客户端基线 vs 本次快照”也会触发一次冷重建。** 证据链：Go 侧把上游 `Set-Cookie` 合并进 Worker 罐（`waa-signer/internal/aistudio/runtime_go.go:338-346,378`），`/v1/proof/mint` 与 `/v1/rpc/send` 用 `DiffCookies` 回传差异（`main.go:988-995,1111-1122`）；Python 侧无条件合并进内存并按脏标记落盘（`browserless_session.py:494-498,744-749,675-695`），下一次 `_cookie_text()`（`:383-388`）即携带新值。下次 `AcquireWorker` 相对冷启动基线判定“已轮换”，按最新凭据冷建 —— 功能正确，仅多一次冷建开销；实际频率取决于上游轮换频率，需实机观测。若需消除，可在 Python 侧区分“客户端主动轮换”与“Worker 回传轮换”（如仅 403/保活路径更新基线），或为滚动凭据引入版本号。

---

## 五、R2 复审记录（2026-11-11，`P2-1` / `P2-2` / `Q-1` / `Q-2`）

- **复审范围**：`waa-signer/internal/poolstate/state.go`、`state_test.go`、`waa-signer/cmd/waa-signer/main.go`、`src/aistudio_api/application/routing_eligibility.py`、`src/aistudio_api/infrastructure/gateway/model_discovery.py`、`tests/unit/test_model_tier_admission.py`。
- **只读声明（R2）**：仅更新本报告，未修改任何代码/配置/测试，未启动真实服务或浏览器，未执行 `git commit`。

### 5.1 门禁实跑（R2）

| 门禁命令 | 实际输出 | 结果 |
|---|---|---|
| `cd waa-signer && go test -count=1 ./internal/poolstate/... ./internal/signerapi/... ./internal/upstreamguard/...` | `ok poolstate 1.220s` / `ok signerapi 1.230s` / `ok upstreamguard 0.638s` | **PASS** (exit 0) |
| `cd waa-signer && go test -count=1 -v ./internal/poolstate/...` | 6 个 Test 函数 / 31 个子用例全部 PASS，含新增 13 组 | **PASS** (exit 0) |
| `gofmt -l waa-signer/cmd/waa-signer/main.go waa-signer/internal/poolstate/state.go waa-signer/internal/poolstate/state_test.go` | 空输出 | **PASS** (exit 0) |
| `cd waa-signer && go vet ./internal/poolstate/...` | 无输出 | **PASS** (exit 0) |
| `uv run ruff check .` | `All checks passed!` | **PASS** (exit 0) |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` | **PASS** (exit 0) |
| `uv run pytest -q tests/unit/test_model_tier_admission.py tests/unit/test_routing_eligibility.py tests/unit/test_instance_pool.py tests/unit/test_instance_manager.py tests/unit/test_browserless_session.py tests/unit/test_browserless_auth_rebind.py` | `117 passed in 1.68s` | **PASS** (exit 0) |
| `git diff --check` | 无输出 | **PASS** (exit 0) |
| `git ls-files config.yaml` | 空输出（未跟踪，符合口径） | **PASS** |

### 5.2 逐项结论

| 编号 | 原问题 | 复验证据 | 结论 |
|---|---|---|---|
| `P2-1` | 热 Worker 差异判定双向读取运行态，可能因旧快照误重建 | 冷启动基线 `main.go:68-71,599`；热复用仅比较基线与请求快照 `:518-527`；`closeLocked` 清基线 `:89`；`state.go:106-132` 纯函数；`state_test.go:159-235` | **已修复关闭** |
| `P2-2` | 持有 `entry.mu` 时调用 `BrowserStorageState` | `main.go:507-607` 锁内无该调用；仅 `State()`（`runtime_native.go:179-183`，RLock 快照）；Cookie 快照为锁内纯内存计算 `:441-450` | **已修复关闭** |
| `Q-1` | 新增 Go 辅助函数无单测 | `state_test.go:108-235` 新增 13 组用例（5 + 8），覆盖缺失/空值/空基线/nil incoming 等方向 | **已修复关闭** |
| `Q-2` | `bool` 被 `isinstance(mode, int)` 当作整数位 | `routing_eligibility.py:142-157`、`model_discovery.py:983-991` 均排除 `bool`；`test_model_tier_admission.py:148-169` 双向回归 | **已修复关闭** |

### 5.3 残余观察

见第四节 `N-7`：热 Worker 基线在“客户端回送 Worker 回传轮换”时会多一次冷重建，功能正确、不阻断交付，建议后续按需引入版本号或区分轮换来源。

---

## 六、最终判定

- **P0 = 0 / P1 = 0 / P2 = 0 / Q = 0 / N = 7**
- **审查结论：PASS**。原 `P2-1`、`P2-2`、`Q-1`、`Q-2` 经 R2 复审确认 100% 修复关闭；全量 Python 门禁、定向 pytest 子集与 Go 单测均通过。`N-7` 为热 Worker 基线的残余性能观察（非正确性缺陷），建议后续迭代评估。

---

## 七、R3 复审记录（2026-11-11，`verify-account` 移动设备验证自动熔断隔离）

- **复审范围**：`src/aistudio_api/domain/errors.py`、`src/aistudio_api/application/routing_pipeline.py`、`src/aistudio_api/application/account_rotator.py`、`src/aistudio_api/application/account_orchestrator.py`、`tests/unit/test_403_auth_refresh.py`，以及 `docs/changelog.md` 的 `[2.1.2]` 增补。
- **只读声明（R3）**：除写入本报告与 `docs/changelog.md` 的 `[2.1.2]` 章节外，未修改任何业务代码、配置或测试；未启动真实生产服务/真实浏览器，未执行 `git commit` / `git push` / `git tag`。运行时核验使用临时脚本于系统 Temp 目录执行，脚本与临时目录已删除，未落仓库。
- **基线**：`HEAD = 390d202`，5 个代码/测试文件均为未提交工作区增量。

### 7.1 门禁实跑（R3，Reviewer 亲自执行）

| 门禁命令 | 实际输出 | 结果 |
|---|---|---|
| `uv run ruff check .` | `All checks passed!` | **PASS** (exit 0) |
| `uv run ruff format --check <errors/routing_pipeline/account_rotator/account_orchestrator/test_403_auth_refresh>` | `5 files already formatted` | **PASS** (exit 0) |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` | **PASS** (exit 0) |
| `$env:PYTHONUTF8='1'; uv run pytest -q tests/unit/test_403_auth_refresh.py tests/unit/test_routing_pipeline.py tests/unit/test_account_orchestrator.py` | `53 passed in 0.38s` | **PASS** (exit 0) |
| `git diff --check` | 无输出 | **PASS** (exit 0) |
| `git ls-files config.yaml` | 空输出（未跟踪，符合口径） | **PASS** |

### 7.2 逐项审计核验

#### 7.2.1 验证信号识别与 `AuthError.verification_required`

- `errors.py:12-19`：`_ACCOUNT_VERIFICATION_MARKERS` 覆盖 `aistudio.google.com/verify-account`、`aistudio.google.com/verify?`、`must be verified before it can continue using the gemini api`、`must be verified before it can use the gemini api`、`account verification required to restore access`、`free_tier_verification_required`。
- `errors.py:22-27`：`is_account_verification_required_message()` 先做空值保护，再统一 `lower()` 后做子串匹配，大小写不敏感。
- `errors.py:30-39`：`AuthError.__init__` 在构造时计算 `verification_required`，因此所有 `AuthError` 子类（含 `SessionExpiredError`）与由 `classify_error` 产生的错误都自动带上该标记。
- `errors.py:280-283`：`classify_error` 将验证标记判定提前到 grpc 8/429、grpc 16/401、grpc 7/403 之前，覆盖 HTTP 403/400 与 grpc 7/9 等组合。
- Reviewer 运行时逐条核验（临时脚本，非仓库文件）：`verify-account` URL、`verify?` URL、`must be verified before it can continue...`、`account verification required to restore access`、`free_tier_verification_required` 均返回 `True`；`HTTP 403: WAA denied` 返回 `False`；`classify_error(403/429/400 + 验证消息)` 均返回 `verification_required=True` 的 `AuthError`。
- 结论：识别逻辑准确，**PASS**（测试覆盖面问题见 `P2-1`、`N-2`）。

#### 7.2.2 `routing_pipeline` 熔断顺序

- `routing_pipeline.py:770-788`：`AuthError` 分支内优先判断 `verification_required`，位于 `tier_allows`（`:789`）与 `_refresh_auth_state`（`:805`）之前，因此不会把验证风控误报为 `TierRequiredError`，也不会发生任何同号重签（0 次 `refresh_auth_state`）。
- 命中后先 `_record_error("session_expired", ...)` 将账号置为失效（`:774`），再记录明确的 `移动设备验证风控` 错误日志（`:775-783`），最后 `_switch_or_raise(..., reason="verification_required")`（`:786-788`）。
- `_switch_or_raise` → `_try_rebind`（`:1131-1138`）在 `verification_required` reason 下复用既有请求期换绑逻辑：记录 switch 预算、清理模板缓存、原位换绑到同 lane 的其它账号（`:1081-1098`）。
- `has_yielded_data=True` 时只记录隔离并抛出异常，不切号（`:784-785`），与既有 `SessionExpiredError` 分支和“已出正文不切号”口径一致。
- 测试证据 `tests/unit/test_403_auth_refresh.py:462-496`：断言 `outcome == "switch"`、`client.refresh_calls == 0`、`"refresh" not in client.events`、改绑到 `acc-2`、`acc-1` 不可用而 `acc-2` 可用、风控日志恰好 1 条，并断言新请求候选只剩 `acc-2`。
- 结论：熔断顺序与切号行为符合任务目标，**PASS**。

#### 7.2.3 账号隔离的持续性与恢复路径

- `account_rotator.py:386-400`：`record_session_expired` 将 `session_expired=True`、`auth_cooldown=now+86400` 并计入错误统计。
- `account_rotator.py:237-259`（`AccountStats.is_available`）：首判 `session_expired` 即返回 `False`；`account_rotator.py:625-637`（`is_account_available`）与 `:639-701`（`select_rebind_account`，`:684-686` 通过 `stats.is_available`）均据此排除该账号。
- `account_orchestrator.py:52-58`：`session_expired` 事件在携带 `reason` 时会透传给 `record_session_expired(reason=...)`，日志落盘包含 `model/surface/reason`（`account_rotator.py:982-990`）。
- 恢复路径：`AccountStats.clear_cooldown`（`:402-421`）清零 `session_expired`；管理台批量导入/重新导入 Cookie 后调用 `rotator.add_account + clear_cooldown`（`routes_accounts.py:1301-1304`、`:1384-1386`、`:1583-1586`），`_clear_account_failure_state`（`routes_accounts.py:195-205`）同时清除持久化 `expired` 与 rotator 锁定；`routes_system.py:396-406` 的手动清除锁定同样可恢复。
- Reviewer 运行时核验（临时脚本）：`record_session_expired("acc-1", reason="verify-account")` 后 `is_account_available("acc-1") == False`、`is_account_available("acc-2") == True`、`select_rebind_account(lane="free", ...)` 返回 `acc-2`；`clear_cooldown("acc-1")` 后恢复 `True` 且 `session_expired=False`。
- 结论：隔离与恢复闭环成立，**PASS**。

### 7.3 问题清单（R3）

#### P2

1. **验证标记缺少自动化回归覆盖，仅一条消息进入测试。** `tests/unit/test_403_auth_refresh.py` 只使用 `VERIFY_ACCOUNT_MESSAGE`（覆盖 `verify-account` URL 与 `must be verified...`），未覆盖 `aistudio.google.com/verify?`、`account verification required to restore access`、`free_tier_verification_required` 三条标记，也未覆盖仅命中 `must be verified before it can use the gemini api` 的分支。Reviewer 已运行时确认当前实现均命中，但后续改动无回归保护。建议改为参数化用例（5 条正例 + 普通 403 负例）。
   - **R4 修复：已修复关闭。** `tests/unit/test_403_auth_refresh.py:462-500` 改为 `@pytest.mark.parametrize`：全部 6 条正例（`verify-account`、`verify?project=123`、`...continue using...`、`...use...`、`account verification required to restore access`、`FREE_TIER_VERIFICATION_REQUIRED`）参数化断言 `is True`；`classify_error` 的 grpc 7/403 与 grpc 9/400 组合亦参数化覆盖。
2. **验证标记判定优先于 429/grpc 8，且隔离为账号级全局生效。** `errors.py:280-283` 在 `grpc_code == 8 or status == 429` 之前返回 `AuthError`，因此任何携带 `free_tier_verification_required` 或验证文案的 429 都会从“额度冷却（到期自动恢复）”升级为“账号 `session_expired` 永久隔离”，且会跨模型/跨 surface 冻结整个账号直到人工清除。当前任务目标即要求账号级隔离，行为符合预期；但若上游未来把验证门收窄到单模型，将造成整号误锁。建议在代码注释/文档中明确这是有意的账号级隔离，或后续按错误详情字段区分模型级与账号级验证。
   - **R4 修复：已修复关闭。** `domain/errors.py:280-287` 补充明确注释：该分支有意置于 429/401/403 判断之前，命中项目/账号级移动设备验证特征即统一升级为账号级 `session_expired` 熔断隔离，直至人工扫码验证或重置锁定，消除“优先级是否为疏漏”的歧义。

#### Q

1. **`account_orchestrator` 的 `session_expired + reason` 透传分支无单测。** 现有 `tests/unit/test_account_orchestrator.py:36-70` 未传 `reason`，只覆盖 else 分支；本次新增的 `record_session_expired(..., reason=...)` 分支无断言。建议补一条 `reason` 透传断言。
   - **R4 修复：已修复关闭。** `tests/unit/test_account_orchestrator.py:74-88` 新增 `test_record_rotator_event_forwards_session_expired_reason`，断言 `record_session_expired("acc-1", model="m", surface="chat", reason="verify-account")` 被精确调用。
2. **`is_account_verification_required_message` 假定入参为 `str`。** `errors.py:26` 直接调用 `text.lower()`；若未来出现 `AuthError(exc)` 之类非字符串入参将抛 `AttributeError`。当前全部调用点为 `str` 且 `pyright` 通过，属防御性加固建议（加 `isinstance(text, str)` 或 `str(text)`）。
   - **R4 修复：已修复关闭。** `domain/errors.py:22-27` 签名改为 `text: object`，首行 `if not isinstance(text, str) or not text: return False`；回归 `tests/unit/test_403_auth_refresh.py` 参数化 `None` / `123` 入参断言返回 `False`，不再抛异常。

#### N

1. 本次 diff 含纯格式化 hunk（`account_rotator.py` 5 处折行、`account_orchestrator.py` 1 处、`AccountStats.from_dict` 1 处），无行为变化。
2. `verify?` 仅在域名限定形式（`aistudio.google.com/verify?`）下命中；裸 `verify?`（无域名）不被识别。Reviewer 运行时实测确认，当前上游报错均携带完整域名 URL，不构成缺陷。
3. `reason` 经 `_summarize_error` 截断至 160 字符（`routing_pipeline.py:331-347`），日志中的完整验证 URL 可能被截断；`verification_required` 分类在异常构造阶段已完成，不受截断影响。
4. `has_yielded_data=True` 时只写入 `session_expired` 并抛出，不切号、不清模板，与既有 `SessionExpiredError` 分支一致（遵守“已出正文不切号”），账号仍被隔离。
5. `countTokens` 走 `record_attempts=False`，`_record_error` 不落账，因此该类请求命中验证风控时不会立即隔离账号，需等下一次生成请求触发。
6. `classify_error` 的验证判定在 `parse_upstream_grpc_error` 之后使用 `clean_msg`；若结构化报文的验证文案只出现在原始 `body` 而未被解析进 `msg`，可能漏判（`clean_msg` 会回退到 `body[:200]`，超长报文截断后仍可能漏判）。属边界观察，建议后续如需可改为同时对原始 `body` 做匹配。
   - **R4 修复：已修复关闭。** `domain/errors.py:282-287` 改为 `is_account_verification_required_message(clean_msg) or is_account_verification_required_message(body)`；回归 `tests/unit/test_403_auth_refresh.py::test_classify_error_detects_marker_beyond_truncated_clean_msg` 构造 300 字符非标准前缀 + `FREE_TIER_VERIFICATION_REQUIRED`，断言仍返回 `verification_required=True` 的 `AuthError`。

### 7.4 最终判定（R3，经 R4 清零）

- **P0 = 0 / P1 = 0 / P2 = 0 / Q = 0 / N = 5**（原 R3 的 `P2-1`、`P2-2`、`Q-1`、`Q-2`、`N-6` 均已修复关闭）
- **审查结论：PASS**。`verify-account` 移动设备验证风控的识别、跳过 `tier_allows`/同号重签、`session_expired` 账号级隔离、请求期换绑切号与后续调度排除均与任务目标一致；管理台重新导入凭据/清除锁定可恢复。定向 pytest、lint、format、pyright、`git diff --check` 与 `git ls-files config.yaml` 全部通过；无 P0/P1 阻断项。
- **R4 门禁实跑**：`uv run ruff format`（3 个文件）、`uv run ruff check .`、`uv run pyright src tests`、`uv run pytest -q tests/unit/test_403_auth_refresh.py tests/unit/test_account_orchestrator.py tests/unit/test_routing_pipeline.py`、`git diff --check` 全部通过。
