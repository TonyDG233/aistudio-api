# REV-C1d — WAA 快照预热 / 大 payload 文本域截断 / 强制重启 独立只读复核

- 复核日期：2026-11-06（UTC）
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 目标分支 / HEAD：`rebuild-upstream` / `a0f783287ec88a89c521dcc1af98333401d22c52`（未提交改动）
- 主工作树：`D:\LLM\self_programming\aistudio-api`（HEAD `39a90391244156836ff26b65056fe846e2f77788`，工作区干净）
- 审计范围：C1d —— `snapshot_generate.js` / `scripts.py` 的 256 字符文本域截断与 WAA 预热、`session.py` / `pooled_session.py` / `capture.py` 的预热状态与日志、`instance_pool.py` / `instance_manager.py` 的强制重启，以及新增/变更单测
- 审计模式：严格离线、只读；未启动服务、浏览器/Chromium、Docker，未发起任何网络请求；未修改 `src/`、`tests/`、`config/`、`defaults/` 或既有文档
- 代码改动：无；未 stage / 未 commit / 未 push
- 唯一写入：本报告的目标树副本与主仓同路径副本（两份字节一致）

## 0. 结论

**Verdict: PASS。**

- A（快照 JS / 参数构造）、B（预热状态 / 捕获日志 / 模板失效）、C（`activate(force=True)` / `restart`）的显式验收项全部满足，证据均可在源码定位。
- D 门禁独立复跑：`pytest` **1029 passed / 0 failed / 0 skipped**（含 69 个 C1d 定点用例）、`ruff check .` **0 error**、`pyright src tests` **0 error**、`bun build` 全 12 个浏览器 JS **exit 0**、`git diff --check` **0**、暂存区空、目标/主仓 HEAD 未变。
- 未发现 P0/P1。存在 2 个非阻塞 P2、5 个 Q、5 个 N，均为边界/覆盖/可维护性项；其中 P2-2 有 JS 低质量兜底缓解，P2-1 仅在快照持续低质量时放大本地调用，不改变“最终报错”的契约。
- 本轮**未提交 / 未 push**。

## 1. 基线、范围与工作树状态

| 项 | 结果 | 证据 |
|---|---|---|
| 目标 HEAD | `a0f783287ec88a89c521dcc1af98333401d22c52` | `git rev-parse HEAD` |
| 主仓 HEAD | `39a90391244156836ff26b65056fe846e2f77788` | `git rev-parse HEAD` |
| 目标未暂存修改 | 11 个文件 | `git diff --name-only` |
| 目标未跟踪文件 | 1 个：`tests/unit/test_snapshot_warmup.py` | `git status --porcelain` |
| 目标状态条目 | 12 = 11 修改 + 1 未跟踪 | 与用户口径“11 modified + 1 untracked”一致 |
| 暂存区 | 空 | `git diff --cached --name-only` 为空 |
| 提交/推送 | 无 | 复核前后目标 HEAD 未变；`## rebuild-upstream...origin/rebuild-upstream` 无 ahead/behind |
| 配置改动 | 无 | `git diff --name-only` 不含 `config.yaml` / `defaults/config.yaml` / `config/` |

## 2. A. `snapshot_generate.js` 与 `scripts.py`

### 2.1 浏览器 JS

| 检查项 | 结论 | 证据（file:line） |
|---|---|---|
| 文本域永远不写超过 256 字符 | **PASS** | `snapshot_generate.js:22-26` 先取 `prompt.slice(-256)`；`:32-35` 只写 `tailPrompt`；`:94` 写 `''`。Python 侧 `scripts.py:29`、`:41` 再截断一次，形成双层保护。 |
| rAF + 40ms 稳定等待 | **PASS** | `snapshot_generate.js:36-42`：存在 `requestAnimationFrame` 时先 rAF，再 `setTimeout(resolve, 40)`；缺失时直接 40ms。 |
| `warmup=true` 时恰好一次本地 dms 预热（占位 hash）再取真实 hash | **PASS** | `snapshot_generate.js:45-46` 定义 1520 阈值与 `'0'.repeat(64)`；`:56-63` `runWarmup()` 只调用一次 `callSnapshot(PLACEHOLDER_HASH)` 后 `wait(40)`；`:68-71` 预热后调用真实 `hash`。JS harness 断言 `calls == [ZERO_HASH, 'realhash']`（`tests/unit/test_snapshot_warmup.py:475-483`）。 |
| 低质量（<1520 或 非 `!` 前缀）先真实调用，再恰好一次预热 + 真实重试 | **PASS** | `snapshot_generate.js:45-51` 质量谓词；`:72-78` 首次真实调用不达标时 `runWarmup()` 后再调用真实 `hash`。harness 断言 3 次调用顺序 `realhash → ZERO → realhash`（`test_snapshot_warmup.py:486-496`）。 |
| 重试后仍低质量必须报错 | **PASS** | `snapshot_generate.js:80-82` 抛 `low_quality_snapshot`；harness 断言抛错且消息含该串（`test_snapshot_warmup.py:499-506`）。 |
| 队列串行化 | **PASS** | `snapshot_generate.js:87-91` 以 `aistudio.snapQueue` 链式串行；harness 断言两次并发调用的 dms 执行严格 start/end 成对（`test_snapshot_warmup.py:509-520`）。 |
| 返回元数据 | **PASS** | `snapshot_generate.js:84` 返回 `{ snapshot, warmup_used, quality_ok: true }`；Python 兼容 dict/str 两种返回（`session.py:466-470`、`pooled_session.py:213-217`）。 |
| `finally` 清空输入框 | **PASS** | `snapshot_generate.js:92-98`；harness 断言最后一次写入为 `''`（`test_snapshot_warmup.py:461-472`）。 |
| 零网络 / 零 `page.goto` / 零 `.say` | **PASS** | 对 `snapshot_generate.js` 检索 `fetch(` / `XMLHttpRequest` / `goto(` / `.say(` / `sendBeacon` / `WebSocket`：无匹配；文件仅调用 `window.default_MakerSuite[snapKey]`。 |

### 2.2 Python 参数构造与 hash 口径

| 检查项 | 结论 | 证据（file:line） |
|---|---|---|
| `SNAPSHOT_PROMPT_TAIL_CHARS = 256` | **PASS** | `scripts.py:29` |
| `build_snapshot_args` 只转发尾部并带 `warmup` | **PASS** | `scripts.py:32-46`；`tests/unit/test_snapshot_warmup.py:79-88` 断言 200KB 输入只留 256 字符、`warmup` 透传。 |
| 截断不影响 hash 输入 | **PASS** | `session.py:442-443` 与 `pooled_session.py:189-190` 均先对完整 `prompt` 计算 `sha256`，随后才调用 `build_snapshot_args`；`test_browser_session.py:351-357` 仍断言 hash 为完整 prompt 的 `sha256`。 |
| 语法/参数契约测试同步更新 | **PASS** | `tests/unit/test_browser_scripts_syntax.py:181-190` 的 `SNAPSHOT_GENERATE_JS` 参数已含 `warmup`；全量 1029 用例通过。 |

## 3. B. Session / PooledSession / Capture

### 3.1 预热状态

| 检查项 | 结论 | 证据（file:line） |
|---|---|---|
| `BrowserSession` 持有 `_last_snapshot_at` / `_force_snapshot_warmup` / `last_snapshot_warmup` | **PASS** | `session.py:140-142` |
| `PooledSession` 持有同一组状态 | **PASS** | `pooled_session.py:69-71` |
| 首次 / >600s / >32KB / forced 触发 warmup | **PASS** | `session.py:75-76`、`:446-453`；`pooled_session.py:49-50`、`:192-199`。测试覆盖首次（`test_snapshot_warmup.py:92-105`）、闲置与 >32KB（`:108-124`）、forced（`:128-149`、`:153-185`）。 |
| `_force_snapshot_warmup` 只消费一次 | **PASS** | `session.py:454`、`pooled_session.py:200`；首次成功快照后下一调用回落到普通路径（`test_snapshot_warmup.py:97-104`）。 |
| 时间戳仅成功时更新 | **PASS** | `session.py:471-474`、`pooled_session.py:218-221`；失败/空快照分支不写 `_last_snapshot_at`。 |
| `refresh_auth_state` 为 403 同账号重试置 force | **PASS** | `session.py:484-495`、`pooled_session.py:230-241` 均在方法入口置 `True`；调用路径 `routing_pipeline.py:705-722` 仅在 `AuthError` 同账号重试前调用。测试 `test_snapshot_warmup.py:128-149`、`:153-185` 断言 403 后下一快照 `warmup=True` 且 force 被清。 |
| 预热阈值常量与逻辑在两处一致 | **PASS（有 Q-3）** | 两处均为 `600.0` / `32 * 1024`、同样的四条件表达式；但常量与 20 行逻辑重复，存在后续漂移风险。 |

### 3.2 捕获日志

| 检查项 | 结论 | 证据（file:line） |
|---|---|---|
| 日志含叹号前缀 / 预热 / 距上次快照 | **PASS** | `capture.py:126-135`：`叹号前缀=...`、`预热=...`、`距上次快照=...`。 |
| 区间计算 | **PASS** | `capture.py:90-98`：前后时间戳均存在时输出 `max(0.0, delta)` 的 `X.XXs`；否则输出 `首次`。测试断言 `12.50s` 与 `首次`（`test_snapshot_warmup.py:214-254`）。 |
| 无敏感内容 | **PASS** | 日志仅 model、长度、布尔、间隔；不记录 prompt / snapshot / headers / cookie。测试以 `SECRET_PROMPT_SENTINEL` 断言不出现（`test_snapshot_warmup.py:222-235`）。 |

### 3.3 `clear_templates(invalidate_base)`

| 检查项 | 结论 | 证据（file:line） |
|---|---|---|
| 参数存在且默认兼容旧调用 | **PASS** | `capture.py:35-52`；`invalidate_base` 默认 `False`，`instance_client.py:78-79` / `client.py:111-116` 的无参调用不受影响。 |
| `invalidate_base=True` 会清上下文标记 | **PASS** | `capture.py:46-47` 先置 `_active_ctx_id=None`，即使模板为空也执行。 |
| 幂等且不重复 INFO | **PASS** | `capture.py:48-52`；空缓存返回 `False` 且不打印；测试 `test_snapshot_warmup.py:257-277` 断言重复调用只产生 1 条“已清空请求模板缓存”。 |

## 4. C. Pool / Manager 强制重启

| 检查项 | 结论 | 证据（file:line） |
|---|---|---|
| `activate(force=True)` 跳过 READY 同账号 no-op | **PASS** | `instance_pool.py:490-499` 的早退条件增加 `not force`；`force=True` 时继续执行。 |
| 关闭旧 Page/Context | **PASS** | `instance_pool.py:501-506` 先做 busy 检查，再 `_close_runtime(slot)`；`_close_runtime:680-715` 先 `session.close()`（内部 `page.close()`，`pooled_session.py:317-320`）后 `dispose_browser_context`，最后触发 retire 回调。 |
| 新 generation / realm | **PASS** | `instance_pool.py:549-583`：`generation+1` 与 `f"{slot_id}-g{next_generation}"`；`_close_runtime` 不清 generation。测试断言 `generation == 2`、`realm_id == f"{slot}-g2"`（`test_instance_pool.py:295-320`）。 |
| `force=False` 行为不变 | **PASS** | `instance_pool.py:490-499` 原四条件保留；测试断言复用同一 context/realm/session 且无 dispose/retire（`test_instance_pool.py:271-293`）。 |
| `restart` 传递 `force=True` | **PASS** | `instance_manager.py:429-440` → `_activate(..., force=True)` → `instance_manager.py:971-1004` → `pool.activate(..., force=force)`。 |
| busy guard | **PASS** | `instance_manager.py:434-436` 先检查 `active_requests > 0`；`instance_pool.py:501-504` 在 `lifecycle_lock` 内再次检查并抛 `InstanceBusyError`。测试用真实 `pool.begin_request` 持有一个在途 handle，断言 `InstanceBusyError` 且 slot 未重建（`test_instance_manager.py:577-600`）。 |
| 测试使用真实池/管理器而非空 fake | **PASS** | 两个新增池用例均构造真实 `InstancePool`（`test_instance_pool.py:272-320`）；管理器用例构造真实 `InstanceManager` + 真实 `InstancePool`，仅以 `FakeCDP`/`FakeSession` 记录 CDP 事件（`test_instance_manager.py:531-575`）。`FakeCDP` 真实创建/销毁 context id，并非只记录调用的 no-op。 |
| 强制重启测试断言清理顺序 | **PASS** | `test_instance_pool.py:315-319`、`test_instance_manager.py:568-574` 均断言 `page.close` 早于 `context.dispose`，并断言旧 context 被 dispose、旧 realm 触发 retire。 |

## 5. D. 测试与门禁实跑

### 5.1 门禁

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **1029 passed in 14.06s**（0 failed / 0 skipped） |
| `uv run pytest -q`（C1d 定点 5 个文件） | **69 passed in 3.48s** |
| `uv run ruff check .` | `All checks passed!`（0 error） |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| `bun build <每个 js> --no-bundle` | 12 个 JS 文件全部 exit `0`（`JS_FILES=12 FAIL=0`） |
| `git diff --check` | exit `0`（仅 LF→CRLF 提示，无 whitespace error） |
| `git diff --cached --name-only` | 空（未 stage） |
| `git rev-parse HEAD` | 目标 `a0f7832...`、主仓 `39a9039...`（复核前后未变） |
| 配置 diff | `git diff --name-only` 不含 config/defaults；`defaults/config.yaml` 仍为 tracked，根 `config.yaml` 未 tracked（与本 diff 无关） |

### 5.2 JS harness 保真度

| 检查项 | 结论 | 证据 |
|---|---|---|
| 执行真实 JS 文本而非重写逻辑 | **PASS** | `test_snapshot_warmup.py:280-428` 将 `SNAPSHOT_GENERATE_JS` 原文注入 `new Function`，由 `bun`/`node` 子进程执行（`:431-453`）。 |
| 覆盖截断、rAF/40ms、预热顺序、低质量重试、报错、队列 | **PASS** | `:461-520` 共 5 个断言函数。 |
| 运行时可用时不跳过 | **PASS** | 本机 `bun` 可用；全量结果 0 skipped，说明 harness 实际执行。 |
| harness 未覆盖的路径 | **见 N-2** | 预热调用失败吞没、无 rAF 回退、错误时 `finally` 清空、1519/1520 边界未单测。 |

## 6. 全部未关闭项（P0 / P1 / P2 / Q / N）

### P0

- 无。

### P1

- 无。

### P2

- **P2-1（外层次数放大 / 错误原因弱化）**：JS 内层已经在低质量时做一次预热重试并抛 `low_quality_snapshot`（`snapshot_generate.js:72-82`），但 Python 外层仍按 5 次“瞬态失败”重试（`session.py:459-478`、`pooled_session.py:204-225`）。持续低质量时，每次外层重试都会重新执行内层 2–3 次 dms 调用，最坏约 10–15 次；最终抛出的是通用 `RuntimeError`（`session.py:480-482`、`pooled_session.py:226-228`），`low_quality_snapshot` 原因只在 debug 日志中出现。功能上仍会失败，不作为阻断项；建议识别 `low_quality_snapshot` 后快速失败或保留原因。
- **P2-2（`BrowserSession` 换页后“首次”预热未重置）**：`_last_snapshot_at` 在 `BrowserSession` 实例上持久化，但 `set_ready_context`（`session.py:167-172`）与 `transition_to_account` 成功换页（`session.py:332-340`）都没有把它重置为 `None`；而 warmup 判定只把 `None` 当作首次（`session.py:449`）。因此 legacy/控制面在同一 `BrowserSession` 上切换账号或重建页面后，新页的首个快照可能不再主动预热（若距上次快照 <600s 且 prompt ≤32KB）。JS 低质量兜底（`snapshot_generate.js:72-78`）仍会在长度/`!` 不达标时补预热，故影响被缓解；池模式每个 `PooledSession` 是新对象，不受影响。

### Q

- **Q-1（`invalidate_base` 未接入生产路径）**：`clear_templates(invalidate_base=True)` 语义与幂等性正确（`capture.py:35-52`），但全仓生产调用均为无参（`instance_client.py:78-79`、`client.py:111-116`、`routing_pipeline.py:689/703/712/784/804/985`），`invalidate_base=True` 只出现在测试中。若 C1d 期望 403/切号路径主动丢弃 base 模板，则尚未接线；若仅作为防御性 API，则可接受。
- **Q-2（32KB 阈值的度量对象）**：`SNAPSHOT_WARMUP_PROMPT_BYTES` 比较的是 `build_snapshot_hash_parts(contents)` 拼接后的文本字节（`session.py:447`、`pooled_session.py:193`），不含 `tools` / `system_instruction` / 完整 JSON body。原始故障场景是 150–260KB 且带 tools 的请求；此类请求的 snapshot prompt 可能远小于 32KB，只会在首次/闲置/403 forced 时预热。当前实现符合“prompt >32KB”的书面口径，但与“大请求”实际体量不完全对应，属残余风险。
- **Q-3（预热常量/逻辑重复）**：`SNAPSHOT_WARMUP_IDLE_S`、`SNAPSHOT_WARMUP_PROMPT_BYTES` 与 4 条件判定在 `session.py:75-76`/`:446-453` 与 `pooled_session.py:49-50`/`:192-199` 各有一份；后续修改阈值时容易只改一处。
- **Q-4（forced 预热在 5 次失败后被消费）**：`_force_snapshot_warmup = False` 在进入尝试循环前执行（`session.py:454`、`pooled_session.py:200`）。若 403 刷新后 5 次快照全部失败，force 已被清掉；若页面随后恢复而会话未重建，下一次快照不会因该 403 再强制预热。`_last_snapshot_at` 未更新，通常仍会因“首次/闲置”或其他 403 重新触发，影响有限。
- **Q-5（JS 队列的可恢复性 / 双重截断边界）**：`aistudio.snapQueue` 以 Promise 链串行（`snapshot_generate.js:87-91`），若某次 dms 调用永不 resolve，链会一直 pending，后续调用即使 Python 侧超时也无法绕过（该队列逻辑在 HEAD 已存在，C1d 的预热调用增加了单次调用次数）。另：Python 按 Unicode 码点截 256（`scripts.py:41`），JS 再按 UTF-16 码元 `slice(-256)`（`snapshot_generate.js:24`）；遇到 astral 字符（emoji）时边界可能截断代理对，产生孤立代理项。对 BMP 中文/ASCII 无影响。

### N

- **N-1（长 prompt hash 断言缺口）**：测试覆盖了“截断后 args 的 prompt 是尾部 256 字符”和短 prompt 的 `sha256` 口径（`test_snapshot_warmup.py:79-88`、`test_browser_session.py:351-357`），但没有直接断言“200KB prompt 的 `hash == sha256(完整 prompt)` 且文本域只收到尾部”。代码路径（`session.py:442-443`、`pooled_session.py:189-190`）正确，属测试覆盖缺口。
- **N-2（harness 未覆盖路径）**：`test_snapshot_warmup.py:280-428` 未模拟：预热调用抛错必须被吞没且不阻断真实调用（`snapshot_generate.js:56-63`）、无 `requestAnimationFrame` 时的 40ms 回退（`:36-42`）、低质量报错时 `finally` 仍清空输入框（`:92-98`）、长度恰好 1519/1520 的边界。
- **N-3（队列测试的时序依赖）**：`test_js_snap_queue_serializes_page_calls` 只断言 dms 调用顺序，不断言文本域写入/清空是否也在队列内；且两个并发调用先各自等待 40ms 再入队，严格 start/end 顺序依赖定时器调度，存在潜在 flake（真实产品路径还有 Python `_snapshot_lock` 兜底）。
- **N-4（`ruff format --check .` 基线红）**：`ruff format --check .` 报告 91 个文件未格式化；对 11 个改动文件单独检查时 5 个被点名，但被点名的行段均不在 C1d diff 内（如 `instance_manager.py:679/736/798/827/1040`、`scripts.py:49`、`test_browser_scripts_syntax.py:212`、`test_instance_manager.py:669+`、`test_instance_pool.py:365`）。用 `git show HEAD:<file>` 取 HEAD 版本复核，同样在这些未改动行段失败，故为**既有基线问题，不是 C1d 回归**。`ruff check .`（“ruff 0”门禁）通过；若团队把 `ruff format --check` 也作为硬门禁，需要单独基线清理。
- **N-5（换页/失败重试覆盖缺口）**：没有用例覆盖 `BrowserSession` 换页后 `_last_snapshot_at` 应重置（P2-2）或 403 forced 预热在 5 次失败后的状态（Q-4）。

## 7. 残余风险与最终裁定

- 显式验收项 A/B/C/D 全部满足；1029 全量用例、ruff、pyright、bun、diff/暂存检查均通过。
- 主要残余风险：P2-1（低质量时外层 5× 放大与原因弱化）、P2-2（legacy `BrowserSession` 换页后首次预热可能不主动触发，JS 低质量兜底缓解）；Q-1（`invalidate_base` 未接线）、Q-2（32KB 度量不含 tools/body）、Q-5（JS 队列悬挂 / 代理对边界）。
- 这些均不改变“截断—预热—低质量重试—报错—强制重启清理”的主路径与门禁结果，故裁定 **PASS**，上述 P2/Q/N 作为后续 C 阶段清理项。

**未提交 / 未 push。** 本轮仅写入目标树与主仓两份报告副本；未执行 `git add` / `git commit` / `git push`。

## 8. C1d P2/Q/N 收口复核（只读，2026-11-06 UTC）

- 目标 HEAD `a0f783287ec88a89c521dcc1af98333401d22c52`（未提交改动；HEAD 未变）；主仓 HEAD `39a90391244156836ff26b65056fe846e2f77788`（未变）。
- 复核模式：严格离线只读；未改 `src/`、`tests/`、`config/`，未 stage/commit/push。唯一写入为本追加段的两份报告副本。

### P2（2/2 解决）

- **P2-1 已解决**：`low_quality_snapshot` 快速失败并保留原因，不再触发外层 5 次放大——`session.py:499-506`、`pooled_session.py:225-230`；用例断言仅 1 次 `evaluate`、0 次 `wait_for_timeout`（`test_snapshot_warmup.py:291-313`）。
- **P2-2 已解决**：`set_ready_context` 重置 `_last_snapshot_at`（`session.py:187`），`transition_to_account` 换页前重置时间戳并失效模板（`session.py:294-295`）；用例 `test_snapshot_warmup.py:376-391`、`:395-437`。

### Q（5/5 解决）

- **Q-1 已解决**：关闭/换页路径 `invalidate_base=True`（`session.py:177-180,294,703`；`pooled_session.py:325`），请求期保持默认 `False`（`capture.py:35`；`instance_client.py:78-79`、`client.py:117`、`routing_pipeline.py:689/703/712/784/804/985` 均无参）；用例 `test_snapshot_warmup.py:540-568`（请求期保留 `_active_ctx_id`）、`:441-464`。
- **Q-2 已解决**：capture 在生成快照前序列化完整 body（contents+tools+system+generationConfig）度量 `payload_size` 并传入（`capture.py:89-119`）；判定取 `max(prompt_bytes, payload_bytes)`（`session_state.py:41`）；150–260KB tools 用例 `test_snapshot_warmup.py:166-190`（payload 落在 150–260KB 且 `warmup=True`）。
- **Q-3 已解决**：阈值常量与 `should_warm_snapshot` 收敛到 `session_state.py:16-42`，`session.py` 与 `pooled_session.py` 共同引用（`session.py:59-64,470-476`；`pooled_session.py:40-43,196-202`）。
- **Q-4 已解决**：`_force_snapshot_warmup` 仅在成功分支清零（`session.py:493-496`；`pooled_session.py:219-222`），5 次全失败后仍保留；用例 `test_snapshot_warmup.py:317-373`。
- **Q-5 已解决**：按码点截断 `Array.from(prompt).slice(-256)`（`snapshot_generate.js:24-26`；`scripts.py:41` 亦按码点），20s `Promise.race` 超时（`snapshot_generate.js:59-71`），队列失败可恢复 `prev.catch(()=>{}).then(run)`（`:109-111`），输入写/清在队列内（`:85-105`）；用例 `test_snapshot_warmup.py:955-962,999-1028`。

### N（N1/N2/N3/N5 解决；N4 为既有基线，非 C1d 项）

- **N-1 已解决**：新增 200KB 全量 hash + 仅转发尾部断言 `test_snapshot_warmup.py:145-162`。
- **N-2 已解决**：harness 补齐预热抛错吞没、无 rAF 回退、报错时 `finally` 清空、1519/1520 边界 `test_snapshot_warmup.py:965-996`。
- **N-3 已解决**：队列用例新增输入写/清顺序断言（写→start→end→清→写→start→end→清）`test_snapshot_warmup.py:1007-1028`。
- **N-4 保持既有基线（非 C1d 项，非回归）**：`ruff format --check .` 仍报告 91 文件（含 6 个改动文件），但 `ruff format --diff` 点名的行段全部位于 C1d 之外的既有代码（`scripts.py:49` 的 probe 签名、`client.py` 的 `_sanitize_model_component`、`instance_manager.py` 既有 tier-sync 段、`test_browser_scripts_syntax.py:212` 既有三元表达式、`test_instance_manager.py` / `test_instance_pool.py` 既有 tier-sync 用例）；新增 `test_snapshot_warmup.py` 格式检查通过；硬门禁 `ruff check .` 为 0。
- **N-5 已解决**：换页重置与 5 次失败后 force 状态均有断言 `test_snapshot_warmup.py:317-373,395-437`。

### 本轮门禁实跑（目标工作树）

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **1047 passed, 0 failed, 0 skipped**（15.86s） |
| `uv run ruff check .` | `All checks passed!`（0 error） |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| `bun build <each js> --no-bundle` | `JS_FILES=12 FAIL=0` |
| `git diff --check` | exit 0（仅 LF→CRLF 提示） |
| `git diff --cached --name-only` | 空（未 stage） |
| 配置 diff | 不含 `config` / `defaults` |
| HEAD | 目标 `a0f7832...`、主仓 `39a9039...`（未变） |

### 签名变更回归检查

- `generate_snapshot(..., *, payload_size=None)` 同步更新 `session_protocol.py:22-24`、`session.py:460-461`、`pooled_session.py:187-188`；生产调用点仅 `capture.py:117-119` 传入，其余无参调用由关键字默认值兼容；`pyright` 0 证明协议实现一致。
- `clear_templates(invalidate_base=False)` 为可选参数，既有 `client.py:117`、`instance_client.py:79`、`routing_pipeline.py:*` 无参调用不受影响。
- `attach_capture_service` 仅 `client.py:117` 接线；未接线时失效为 no-op，不影响既有路径。

### 最终裁定

- P2：2/2 解决；Q：5/5 解决；N：N1/N2/N3/N5 解决，N4 为既有基线、非 C1d 项。**C1d 范围内未解决 P2/Q/N = 0**。
- **Verdict: PASS。**

**未提交 / 未 push。**
