# REV-2.2.0.5 — LRU 全池大循环选号修复与预防性主动轮换（前后端 + 移动端适配）只读复审报告

- 步骤：Stage 2.2.0 维护项 — 5 号乒乓修复（LRU 全池大循环）+ 预防性主动轮换（`proactive_switch_mode / threshold / groups`）前后端打通与移动端防挤压适配（`REV-2.2.0.5`）。
- 复审目标：**独立只读复审**。不采信任何 Worker 自填结论，直接从源码、`git show 581bf55` 差异与实跑门禁逐行复核；覆盖初审遗留 `P2-1`、`P2-2`、`Q-1..Q-5`，以及本轮对 `Q-6`、`N-5`、`N-6` 修复的闭环核验。
- 工作树状态（闭环复审时实测）：**HEAD = `581bf55`**；`Q-6 / N-5 / N-6` 的修复为工作树中独立 Worker 的未提交改动（`instance_manager.py`、`routing_pipeline.py`、`tests/unit/test_routing_pipeline.py`），由本 Reviewer 只读核验后写入本报告，**Reviewer 未提交任何代码**。
- 权威依据：`AGENT.md` §4/§5/§7/§10；`docs/dev/rebuild/06-decision-log.md`、`08-09-decision-record.md` D-01..D-14。
- 复审方式：**100% 独立只读**。阅读源码与差异、实跑后端门禁；本轮**未启动真实服务、未启动浏览器引擎、未连接任何真实 Google 账号、未执行 `git commit/push`**。
- 复审日期：2026-11-14（含 `Q-6 / N-5 / N-6` 闭环复审）。
- **独立复审结论：PASS**（0 P0 / 0 P1 / 0 P2；原 P2-1、P2-2、Q-2、Q-3、Q-4 已闭环；本轮 `Q-6 / N-5 / N-6` 经独立核验确认闭环；仅剩 Q-1、Q-5 与 N-1..N-4 为非阻塞残留）。

> 流程合规备注：此前由返工 Worker 自行把本报告 `CONDITIONAL` 改为 `PASS`，违反「改码后须由独立只读 Reviewer 复审签署」的铁律。本报告为该违规行为的**独立重审与再签署**：结论来自源码/差异/实跑，不沿用 Worker 自填文字。经独立核验，其实现结果确已满足 PASS 条件，但原报告的若干 `file:line` 与页首元信息存在偏差，已在本报告中纠正（见 N-7）。

## 1. 独立复审结论摘要

- **5 号乒乓已修复（真实 LRU）**：`AccountRotator.select_rebind_account` 排序键改为 `last_used` 优先、`(tier_rank, total_requests, model_requests_total)` 仅作 tie-break（`account_rotator.py:736-744`）；`InstanceManager._pick_account` 同样按 rotator 的 `last_used` 取最久未用（`instance_manager.py:1236-1245`）。单测 `test_rebind_selection_cycles_through_all_five_accounts` 证明 5 账号 `a1..a5 -> a1` 整圈轮转。
- **按模型 429 隔离正确**：`record_rate_limited` 把 `last_used = now`（`account_rotator.py:324-328`）推到 LRU 尾部，同时 `is_available(model)` 的模型级冷却只排除该模型（`account_rotator.py:253-257`）；`test_rebind_selection_respects_per_model_429_cooldown` 双向断言成立。
- **预防性主动轮换前后端贯通**：配置三件套在 `defaults/config.yaml:131-133`、`defaults/config.schema.json`、`model_defaults.py:1462-1524`、`web/src/components/config/schema.ts:759-791`、`web/src/components/settings/SchedulingCard.vue:209-...` 全部落地。
- **P2-1 / P2-2 已真实闭环**（独立复核详见 §3.1 / §3.2）：`require_idle` 的锁内二次确认与无锁快速预检均确认到位，且**初审假设的「retire 后 activate 抛 busy 不回滚 → 死槽」在 `require_idle=True` 路径下不可达**（证明见 §3.1）。
- 问题统计（闭环后）：**P0 = 0，P1 = 0，P2 = 0；Q = 2（Q-1、Q-5，均非阻塞）；N = 4（N-1..N-4）**；`Q-6`、`N-5`、`N-6` 已于本轮独立核验关闭，`N-7`（报告元信息偏差）已随本报告纠正（见 §4.1）。

## 2. 独立核查的变更范围

`git show 581bf55 --stat` 实测（关键项）：

| 文件 | 变更 | 核心内容 |
|---|---|---|
| `src/aistudio_api/application/account_rotator.py` | +39 | 严格 LRU 排序 + `allowed_groups`；`record_rate_limited` 推进 `last_used`；`mark_account_used` / `get_account_rotator` |
| `src/aistudio_api/application/instance_manager.py` | +121 | `reassign(require_idle=...)`；`_pick_account` LRU；槽位连击读写/重置 |
| `src/aistudio_api/application/routing_pipeline.py` | +288 | `_call_reassign`、`_heal_unavailable_instances` 传 `require_idle=True`、`_proactive_rotate_instances` 与无锁预检 `_proactive_rotation_due` |
| `src/aistudio_api/infrastructure/gateway/instance_pool.py` | +29 | 槽位 `consecutive_requests`，成功自增、换绑/退役清零 |
| `src/aistudio_api/infrastructure/gateway/model_defaults.py` | +97 | 三键规范化（mode/threshold/groups），整型 float 收窄 |
| `defaults/config.yaml` / `defaults/config.schema.json` | +8 / +16 | 三键默认与 schema |
| `web/src/components/settings/SchedulingCard.vue` | +219 | 主动轮换卡片（模式/阈值/分组/手动添加） |
| `web/src/components/config/schema.ts` / `web/tests/configTooltip.test.ts` | +33 / +16 | schema 与默认值断言 |
| `tests/unit/test_{account_routing,routing_pipeline,features_config}.py` | +165 / +518 / +76 | LRU、主动轮换、配置规范化单测 |

> 说明：`static/assets/*`、`docs/.../INDEX.md`、`P2-routing/index.md`、两张截图随提交入库，属构建产物/文档留档，不在本次独立复审的白名单源码范围，但前端门禁已实跑确认源码可编译。

## 3. 遗留问题独立复核（P2-1 / P2-2 / Q-1..Q-5）

### 3.1 P2-1「需空闲的改绑入口」— 独立核验：**已闭环**

复核点与证据（关键：初审提出的「死穴」是否可达）：

1. **入口签名与锁内二次确认**：`InstanceManager.reassign(..., require_idle=False)` 在 `async with self._lifecycle_lock` 内先读槽位：若 `require_idle and slot.active_requests > 0` 直接 `return self._view(record)`（`instance_manager.py:495-518`）。忙槽不会被 `_retire_record`。
2. **「先 retire 再 activate 抛 busy 却不回滚」是否可达 —— 结论：在 `require_idle=True` 路径下不可达，非死槽**：
   - 第二次 `slot.active_requests > 0` 判定与其后的 `await self._retire_record(record)` 位于 `instance_manager.py:544-548`。
   - 在初次空闲判定（`:513-518`）到第二次判定（`:544-548`）之间**不存在任何 `await`**：`_record_for_account`（`:1451-1466`）、`_accounts.get_account`、`_pool.get` 均为同步函数，中间无挂起点。
   - 因此单事件循环下 `active_requests` 不可能在这段区间内变化；`require_idle=True` 时第二处 `>0` 分支（唯一调用 `_retire_record` 的地方）**不可达**，`_retire_record` 不会先行执行。
   - 故 `except InstanceBusyError: if require_idle: return self._view(record)`（`:551-556`）返回的是**未被退役**的槽位（`account_id`、状态保持原样），不会出现「已关会话却返回旧 current」的死槽。
3. **`_activate` 抛 busy 时的状态回滚**：`_activate` 在首个 `await` 前同步写 `record.state = STARTING`（`instance_manager.py:1316-1317`）；若 `pool.activate` 在 `instance_pool.py:618-621` 因在途请求抛 `InstanceBusyError`，`_activate` 捕获并恢复 `record.state = previous_state`（`instance_manager.py:1334-1337`），随后 `require_idle` 分支返回视图、账号保持原号，仍是可用槽。
4. **生产准入路径的额外护栏**：路由 `acquire` 走 `manager.begin_request`（`routing_pipeline.py:787`），后者在 `record.state is not ACTIVE` 时直接拒绝（`instance_manager.py:760-767`）。因 `_activate` 已把状态置 `STARTING`，并发请求无法在 `pool.activate` 内部的 `_close_runtime` 窗口（`instance_pool.py:624`）抢入旧会话。`require_idle` 是「锁内二次确认」，`record.state` 是「二次护栏」，双保险成立。
5. **单测覆盖**：`test_require_idle_reassign_leaves_busy_slot_untouched`（`tests/unit/test_routing_pipeline.py:2224-2269`）用真实 `InstanceManager`+`InstancePool`：占住在途后 `require_idle=True` 返回原账号、状态 `ACTIVE`、`cdp.disposed == []`，释放后可正常换绑；`test_heal_passes_require_idle_to_reassign`（`:2202-2220`）验证自愈传入 `require_idle=True`；`test_proactive_free_only_rotates_idle_free_slot`（`:1852-1891`）断言 `require_idle_calls == [("inst-1", b)]`。

**结论：P2-1 已真实闭环。** 原「两处判定间无 `await`」的隐含前提已由收口 Worker 升级为显式锁内护栏（`instance_manager.py:545-550`：第二次忙判定内 `require_idle` 直接返回、绝不 retire），`N-5` 已于本轮独立核验关闭（见 §4.1）。

### 3.2 P2-2「全局锁串行化普通请求」— 独立核验：**已闭环**

- `_proactive_rotate_instances` 在取锁前先做廉价返回：`mode == "off"` 直接 `return 0`（`routing_pipeline.py:1524-1529`）；`groups` 模式分组为空直接 `return 0`（`:1542-1550`）；随后调用无锁预检 `_proactive_rotation_due`，未命中即 `return 0`，**完全不进入 `_heal_lock`**（`:1559-1564`）。
- `_proactive_rotation_due`（`:1648-1672`）只做同步读取：`_live_instance`（`:1388-1415`，异常吞掉）、`_instance_active_requests`、`_proactive_mode_allows`、`_instance_consecutive_requests`（均带异常兜底）；无 `await`，不会抛异常外泄。
- **无漏判**：预检条件（空闲 + 达到阈值 + 模式允许）是锁内再次校验（`:1569-1579`）的严格子集，命中后仍在锁内以 `live` 重读复核，不会因状态漂移而误伤；仅在状态漂移时产生一次「空进锁后 return 0」，可接受。
- **单测覆盖**：`test_proactive_fast_path_skips_global_heal_lock`（`:2073-2106`）把 `_heal_lock` 换成「一进即失败」的替身，阈值未达时 `begin()` 正常完成、无换绑，证明普通请求不再串行于全局锁。

**结论：P2-2 已真实闭环。**

### 3.3 Q-1..Q-5 独立状态

| 编号 | 独立结论 | 证据 |
|---|---|---|
| **Q-1「连续成功」语义偏宽** | **仍开放（非阻塞）** | 槽位连击仅在成功时自增（`routing_pipeline.py:828-853` → `instance_manager.py:1107-1120` → `instance_pool.py:381-390`）；失败路径（429/5xx/空响应）不清零，仅在换号（`instance_pool.py:623-630`）、退役（`:799,823`）、轮换无替代（`routing_pipeline.py:1602,1606`）时清零。语义实为「自上次换绑以来的累计成功数」。 |
| **Q-2 无替代账号每请求重扫** | **已闭环** | `account is None` / 同号时 `_reset_instance_consecutive_requests` 清零（`routing_pipeline.py:1599-1607`、`instance_manager.py:1122-1142`、`instance_pool.py:393-400`）；`test_proactive_rotation_keeps_account_when_no_alternative`（`tests/unit/test_routing_pipeline.py:1929-1962`）断言 `reset_calls == ["inst-1"]`、streak 归 0。 |
| **Q-3 先改 LRU 后换绑** | **已闭环** | `_touch_account_last_used(live.account_id)` 移到换绑成功且 `getattr(updated,"account_id",...) == target_id` 之后（`routing_pipeline.py:1619-1632`）；失败分支 `except` 即 `continue` 不动 LRU（`:1611-1618`）；`test_proactive_rotation_does_not_touch_lru_when_reassign_fails`（`:2110-2148`）断言 `stats_a.last_used == 10.0`。 |
| **Q-4 阈值仅接受 int** | **已闭环** | `_normalize_proactive_switch_threshold` 先收窄整值 `float`（如 `20.0 -> 20`），布尔/非整 float/字符串/越界回退 15（`model_defaults.py:1476-1497`）；`test_proactive_switch_accepts_integral_float_threshold`（`test_features_config.py:854-864`）。 |
| **Q-5 `_pick_account` 改变「活跃账号优先」** | **仍开放（预期语义变更，非阻塞）** | `min(eligible, key=(last_used, index))`（`instance_manager.py:1243-1252`），`last_used` 主导、`index` 仅做时间戳相等的 tie-break。达成本轮「整池轮转」目标，但改变了旧「活跃优先」启动语义，建议在域文档明确。该函数 docstring 已于本轮修正为「LRU 主导、等时间戳回退调用方顺序」（`instance_manager.py:1210-1218`），`N-6` 关闭。 |

## 4. 问题清单（全量）

### P0（阻塞发布）

无。

### P1

无。

### P2

无。（原 P2-1、P2-2 经独立复核确认闭环，见 §3.1 / §3.2。）

### Q（质量/风险，非阻塞，仍开放）

- **Q-1**「连续成功」语义偏宽：仅成功自增、失败不清零，实为「自上次换绑以来的累计成功数」。不影响防风控目标，建议文档措辞对齐或失败时清零。
- **Q-5** `_pick_account` 以 `last_used` 主导，改变了旧「活跃账号优先」语义；为预期变更，建议域文档固化。

### N（细节，仍开放）

- **N-1** 连击计数按「槽位」而非「账号×模型」维度累计；同账号 `force` 重启不清零（`instance_pool.py` activate 仅在 `previous_account_id != account_id` 时清零），设计可接受。
- **N-2** `_normalize_proactive_switch_groups` 接受 `set/frozenset`，迭代顺序不确定；仅影响持久化列表顺序，不影响分组过滤。
- **N-3** 前端阈值 `input` 每次按键即夹紧到 `[1,1000]`（`SchedulingCard.vue:562-566`），输入「0」会被立即纠正为 1，属轻微 UX 细节。
- **N-4** 前端 `accountsApi.listGroups()` 失败时静默保留 `default` + 已保存分组名，不阻断编辑，按设计可接受（`SchedulingCard.vue` `loadProactiveGroups`）。

### 4.1 本轮闭环核验：Q-6 / N-5 / N-6（独立只读）

> 三项修复由独立业务 Worker 完成（未触碰任何 `docs/` 文件）；本 Reviewer 只读核验源码并实跑门禁后写入结论。修复为工作树未提交改动，HEAD 仍为 `581bf55`。

| 编号 | 原问题 | Worker 修复（file:line） | 独立核验结论 | 证据 |
|---|---|---|---|---|
| **Q-6** | 自愈换绑后未校验账号，忙竞争导致丢绑时仍误计「自愈完成」 | `routing_pipeline.py:1494-1504`：`getattr(updated, "account_id", target_id) != target_id` 时记录「死槽自愈跳过」并 `continue`，**不**进 `bound`、**不** `healed += 1` | **已闭环** | 新增 `test_heal_does_not_count_race_dropped_rebind`（`tests/unit/test_routing_pipeline.py:2223-2265`）：假 Manager 在 `require_idle` 后原样返回旧号，断言 `healed == 0`、`require_idle_calls == [("inst-1","healthy")]`、账号仍 `dead`、`reassign_calls == []`；纳入本轮 140 passed |
| **N-5** | `require_idle` 安全性依赖「两次空闲判定间无 `await`」的隐含前提，未来易回归为死槽 | `instance_manager.py:545-550`：第二次 `active_requests>0` 判定内新增 `if require_idle: return self._view(record)`，位于 `_retire_record`（`:553`）**之前**，忙槽在 `require_idle` 路径下永不 retire | **已闭环** | 该分支与首次判定（`:513-518`）对称；即便未来插入 `await`，`require_idle` 也只会原样返回、不会「先关会话后返回」；`test_require_idle_reassign_leaves_busy_slot_untouched`（`:2268-2314`）仍通过 |
| **N-6** | `_pick_account` docstring 与实现相反（称 LRU 仅破并列，实为 LRU 主导） | `instance_manager.py:1210-1218`：改写为「最小 `last_used` 胜出（LRU），等时间戳回退调用方顺序（活跃优先）」 | **已闭环** | 与实现 `min(eligible, key=_lru_key)`（`:1243-1252`，`key=(last_used, index)`）逐字一致 |

> **N-7 已处理**：原报告页首元信息与 `file:line` 偏差已随本报告校正（如 `_pick_account` 实际 `:1204-1252`、`reassign` 实际 `:495-575`）。

## 5. 与既有决策/红线的一致性（独立核对）

| 检查点 | 结论 | 证据 |
|---|---|---|
| Free/Pro lane 不混用 | 通过 | `_proactive_mode_allows` 按 `route.paid` 判 lane（`routing_pipeline.py:1674-1693`）；`reassign` 内 `instance_lane_for_tier != record.lane` 硬拒（`instance_manager.py:521-524`） |
| 不越 tier 区间换绑 | 通过 | 选择器携带 `only_tier/min_tier/max_tier/required_tiers`（`routing_pipeline.py:1581-1591`）；`reassign` 复核 `accepts_tier`（`instance_manager.py:525-530`） |
| `active_requests` 为唯一并发账本 | 通过 | 主动轮换只读 `instance_active_requests`，未新增第二套并发账本；新增的 `consecutive_requests` 为业务计数（`instance_pool.py:182,381-400`） |
| 不恢复蓝绿/后台自动 rebind/rebalance | 通过 | 主动轮换在 `begin()` 准入期同步执行，无后台 task、无并行新 Page |
| 原账号可复用、不锁定 | 通过 | `mark_account_used` 只推进 `last_used`、不写冷却（`account_rotator.py:746-756`）；`test_proactive_free_only_rotates_idle_free_slot` 断言 `is_available(...) is True` |
| 不增加同 IP 高频（4 号初衷） | 通过（设计约束） | 主动轮换仅在空闲时换号，未改动 `_switch_backoff`；仍受既有 3–5s 退避与阈值节流约束 |
| 实机/真实账号红线 | 未触碰 | 本轮仅离线源码阅读 + 单测 + 前端门禁；未启动真实服务/浏览器/上游 |

## 6. 门禁命令实跑结果（独立复审 + 闭环复审两轮；Windows 11 + PowerShell 7）

| # | 命令 | 实际结果 | 退出码 |
|---|---|---|---|
| 1 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync ruff check .` | `All checks passed!` | 0 |
| 2 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations` | 0 |
| 3 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync pytest tests/unit/test_account_routing.py tests/unit/test_routing_pipeline.py tests/unit/test_features_config.py -q` | `140 passed in 1.78s`（含本轮新增 Q-6 回归用例，较上轮 +1） | 0 |
| 4 | `bun run lint`（`workdir=web`） | `eslint src --max-warnings 0`，无告警/错误 | 0 |
| 5 | `bun run type-check`（`workdir=web`） | `vue-tsc -b`，无错误 | 0 |
| 6 | `bun test`（`workdir=web`） | `97 pass / 0 fail / 829 expect()`（11 files） | 0 |
| 7 | `git diff --check` | 无输出（无空白错误） | 0 |

> 闭环复审轮（Q-6/N-5/N-6）实跑后端四项 `ruff / pyright / pytest / git diff --check` 全部通过；pytest 由 139 增至 140（新增 Q-6 用例）。本轮 Worker 仅改后端 `.py` 与后端测试，前端源码未改动，故第 4–6 行前端门禁沿用上一轮结果。

## 7. UI 沙盒实测（说明）

- 本轮为**独立只读复审**，按红线**未再次启动** `--ui-debug` 服务、未拉起浏览器引擎，因此不复跑 Playwright 四分辨率实测。
- 初审留档的 UI 沙盒数据（`SchedulingCard.vue` 模式/阈值/分组交互、`GET /config` 持久化、1440/1024/768/375 四分辨率 `getBoundingClientRect()` 无溢出，及 `reviews/assets/rev-2.2.0.5-proactive-switch-{desktop-1440,mobile-375}.png` 两张截图）**作为初审证据保留**，不由本轮重新签署。
- 本轮可独立确认的部分：前端源码可编译、lint/type-check 无错、`bun test` 97 项全绿；新增卡片的结构与交互分支（`SchedulingCard.vue:209-234, 562-591, 1011-1137`）与 `schema.ts:759-791` 一致，`accountsApi.listGroups()` 存在且返回 `Promise<string[]>`（`web/src/api/accounts.ts:97`），`Plus` 图标与 `asArray/asString/asNumber` 均已正确导入（`SchedulingCard.vue:2-30`）。若需以本轮名义签署 UI，应另行获准启动 `--ui-debug` 沙盒并补测。

## 8. 只读声明

- 本轮**仅阅读**源码/测试/配置/提交差异，**未修改**任何业务代码、测试、配置或前端源码。
- 本轮**唯一写入**的文件为本报告：`docs/dev/rebuild/domains/P2-routing/reviews/REV-2.2.0.5-lru-and-proactive-switch.md`。
- **未执行** `git commit` / `git push` / `git checkout` / `git reset` / `git add`；**未启动**真实服务或浏览器引擎；**未连接**真实 Google 账号或发起真实上游请求。
- 所有结论均附 `file:line` 与可复现命令；门禁结果为上述命令的实跑输出，未预填、未伪造证据；本报告不含本机绝对路径。

## 9. 建议的后续动作

已闭环（本轮，§4.1）：**Q-6**、**N-5**、**N-6** 已由独立 Worker 修复并经本 Reviewer 只读核验关闭；**N-7**（报告元信息/行号偏差）已随本报告校正。

仍为非阻塞残留：

1. **Q-1**：失败时清零连击，或将文档口径表述为「自上次换绑以来的累计成功数」。
2. **Q-5**：在域文档明确 `_pick_account` 的 LRU 语义（「活跃账号优先」已非主导）。
3. **N-1..N-4**：细节项，按需处理。

闭环复审结论：**PASS**（0 P0 / 0 P1 / 0 P2；仅剩 Q-1、Q-5 与 N-1..N-4 为非阻塞）。
