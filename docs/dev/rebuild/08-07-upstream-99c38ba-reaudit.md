# 08-07 Upstream 99c38ba 重审

状态：2026-10-27 文档重构产物。结论仅到“可提交独立复审”，不宣告 handoff，也不宣告 Gate 0 已通过。

## 1. 基线与拉取记录

| 项 | 值 |
|---|---|
| 仓库 / 分支 | `D:\LLM\self_programming\aistudio-api` @ `rebuild` |
| 重构前 HEAD | `8e0ab11` |
| 旧审计基线 | `4623109b97d14db30e304005fe1d52c29a46a7c2` |
| fetch 命令 | `git fetch upstream --prune` |
| fetch 时间 | 2026-10-27T18:48:10Z（本地 2026-10-28 02:48:10） |
| fetch 结果 | 成功；无新 ref（`upstream/master` 已指向最新），`.git/FETCH_HEAD` 已刷新 |
| 实际 `upstream/master` | `99c38baefc84d8cb66b05c28ede8a49ce050bfb2` |
| 新上游日期 | 2026-10-25 |
| 参考 worktree | `.kilo/worktrees/rebuild-upstream` @ `9b5cdea`（只读参考，本次未修改） |

`99c38ba` 与 `git rev-parse upstream/master` 实测一致；`4623109` 作废为计划基线，只保留在历史审计说明中。

### 1.1 提交清单（`4623109..99c38ba`）

```text
99c38ba feat: account grouping, preset management, model quota weighting, and multi-tier preemption
b8af406 feat: port Tier-1 protocol fixes, WAA hash alignment, upstream Gzip, and network resilience
296680f fix(gateway): await session readiness in template capture and remove aggressive watchdogs
```

### 1.2 diffstat

```text
76 files changed, 4200 insertions(+), 1190 deletions(-)
```

主要文件域：`src/aistudio_api/{config.py,application,api,infrastructure}`、`web/src`、`tests/`、`defaults/config.yaml`、`config.yaml`、`docs/WIRE_SPECIFICATION.md`、`docs/BOTGUARD_VERIFICATION_CHAIN.md`。本次重审已用 `git diff` 逐项复核下列变化，不只依赖摘要。

## 2. 影响矩阵

动作含义：`adopt` 采上游原生；`rewrite` 采用其能力但必须按本仓约束改写；`keep` 本仓既有决策不变；`disable` 不启用。

| 上游变化（99c38ba 实证） | 决策包 | 动作 | 理由 | 需同步的文档/测试/接口 |
|---|---|---|---|---|
| `AccountMeta` 新增 `enabled/group/notes`；`AccountPreset` + `presets.json`；`AccountService` 分组/预设 CRUD；`routes_accounts` 新增 toggle/group/notes/groups/presets 路由；`get_active_account` 只回退 `enabled` 账号 | P1 | adopt | 纯账号域能力，不改变实例模型；禁用账号不进入候选是必要前置 | `12 §1/§3`、`13`、`05 §3`；`test_account_presets.py` |
| `AccountStats` 改为 `Account × Model × Date`（`daily_models`）；`resolve_tier_quota` 软配额；`_score_candidate` 使用 `tier_weights/stickiness/drip_penalty`；`required_tiers` 抢占 | P2 / P9 | rewrite | 打分与软配额只能作为“已有实例上的候选排序/准入”；不得新增请求实例、不得后台改绑、不得另立并发账本 | `12 §1/§3`、`10` 状态机、`05 §4`；`test_tier_preemption.py` 改写为 active_requests 约束下的准入测试 |
| 付费模型无合格付费账号时，本地 `403` fast-fail（不发起上游请求） | P2 / P5 | adopt | 与“已出正文不切号、429 才 503”不冲突；403 是准入拒绝，不是 429 耗尽 | `12 §1`、`10` 错误口径、`06 P2-F` |
| 移除 `browser_idle_timeout`、idle 回收任务、进程 watchdog、liveness checker、CDP ping | P3 / P6 | adopt（移除）+ rewrite（索引行） | 上游已删除这些后台自治路径，正好符合“无后台自动 stale 复活、无自动回收”的硬约束；本仓不再保留相关配置键。索引 `D03-004/D03-009/D08-022` 按 rewrite 处理，只保留进程组/Job Object/atexit、页面失败即失败、消息唤醒 SLEEPING/UNSTARTED | `12 §2/§3`、`05 §4`、`06 P6-Q6` 新裁决；`06-test-disposition.csv` 中 browser_engine/cdp_client 改 rewrite，删除 watchdog/liveness 用例 |
| `capture._ensure_template` 改为 `await session.ensure_ready()`；`close_context` 等待释放；只清理 `about:blank` 备用页 | P3 | adopt + rewrite | adopt `ensure_ready` 与 `about:blank` 清理；rewrite `close_context`：只保留关闭后等待释放，不采用 blue-green 的 `create_new_page`/`_retiring_contexts` 后台 drain；不改变 1 账号/1 Context/1 Page/1 surface | `12 §2`、`05 §4` |
| `session.py:211` 仍保留 Blue-Green：切号时先 `create_new_page` 建新页，再把旧 context 放入 `_retiring_contexts` 后台 drain（最长 12s） | P3 | rewrite | 该路径会短暂存在两页/两 context，且是后台切换，违反 1 账号 + 1 Context + 1 Page + 1 surface、同账号 chat/build 不并存、手动 rebind 先 `RETIRING→CLOSED→STARTING`、禁止后台自动 rebind。保留 cookie 注入、BotGuard bootstrap、模板捕获与 `ensure_ready`，但必须先关旧 context/page 再建新页 | `05 §4 D3/D4`、`10` 状态机、`12 §2`；新增 rebind 状态测试，禁止复制 `_retiring_contexts`/先建新页再 drain |
| 传输 `>=96KB` 自动 gzip；CDP 网络失败证据；page 绑定 `account_auth_file` 作 cookie 回退；空 200 抛 `BrowserTransportError`；malformed 归错误 | P5 / P3 | adopt | 提升真实性与可诊断性；不改变 capture/replay 架构 | `12 §4`、`10` PG 门槛、`test_stream_transport.py` |
| 流式不再遇 `finish_reason` 提前 break；`WIRE_FINISH_REASON_CONTINUING=22` | P5 | adopt | 修复长流提前截断；仍保留“已出正文不切号” | `12 §4`、`10` 状态机；`test_stream_transport.py` |
| `cached_content→session_token`、`location→request_context`、新增 `SEED=18`、角色守卫、结构化 `functionCall/functionResponse`、per-chunk `usage/response_id`、`to_wire` 的 thought/signature 对所有 part 统一写入 | P5 | adopt | 上游协议层已覆盖 fork 的同类修补，避免重复实现；F-01/F-04 由上游原生实现承担，测试仍须证明 | `12 §4`、`10` PG、`06 P5` 追加裁决；`test_wire_codec.py`、`test_wire_parser.py` |
| 新增 `rotator`、`paid_model_rules`、`tier_quotas`、`account_presets`、`defaults/config.yaml`、video/imagen profile；超时默认 `120→600` | P9 | adopt | 原生配置面；但 rotator 缺键时上游直接 `RuntimeError`，必须纳入 P9-pre fail-fast | `12 §3`、`10` Gate0.5、`06 P9` |
| `ModelStatsResponse` 新增 `reasoning_tokens/cached_tokens/finish_reasons/account_breakdown`；`StatsTotals` 新增 reasoning/cached；Gemini usage 新增 `cachedContentTokenCount` | P7 / P5 | adopt | 统计字段直接采上游；`account_breakdown` 属统计 REST 响应，不是事件账本字段 | `12 §4`、`10` 验收；`test_model_stats_v2.py` 迁移时更新 |
| 新增 `test_account_presets.py`、`test_tier_preemption.py`、`test_stream_transport.py` 扩展；`test_cookie_import.py` 新增 benefit/email probe monkeypatch；删除 watchdog/idle/liveness 测试 | P10 | rewrite（处置） | 新测试须按本仓约束改写；cookie import 必须补 monkeypatch 防止真实探测；删除项确认不再复活旧契约 | `06-test-disposition.csv`（新增 2 行，另有 4 行改 rewrite）、`13` |
| 上游 API Key 语义：`config.yaml` 与 env 均无 Key 时**免密开放** | P8 / P9 | rewrite | 与本仓“默认拒绝，显式空 Key 才开放”冲突；P9-pre 必须改写为默认拒绝 | `12 §3`、`10` 状态机、`13` |

## 3. 冲突裁决（逐项闭合，不留 pending）

### 3.1 账号分组 / 预设 / 软配额 / tier 抢占

- `enabled/group/notes`、预设 CRUD、UI 与 `presets.json` 采用上游原生；禁用账号不进入候选，`get_active_account` 只在 `enabled` 中回退。
- 软配额 `resolve_tier_quota` 只用于候选排序与可解释的准入提示；不新增 quota 池、不产生额外请求实例、不做后台改绑。
- `tier_weights/stickiness/drip_penalty` 的输入必须来自现有实例池；`required_tiers` 抢占只允许在“同一候选集合内换序/过滤”，不得为了抢占新建 Context/Page/surface。
- 付费模型无付费账号的本地 `403` 保留；它与 429 耗尽 `503` 是两类结果，不得合并。
- 账号分组/预设/统计/UI 可作为原生能力采用；配额打分/tier 准入必须服从 1 账号 + 1 Context + 1 Page + 1 surface、同账号 chat/build 不并存、`active_requests` 唯一账本。

### 3.2 idle timeout 移除

- 上游已删除 `browser_idle_timeout`、idle 回收、watchdog、liveness checker 与 CDP ping；本仓不恢复这些键或任务。
- 状态机保留 `SLEEPING/UNSTARTED` 作为“可被消息唤醒”的状态，但进入 `SLEEPING` 只由显式释放路径产生，不存在定时自动回收。
- `active_activity` 旧语义是“阻止 idle 回收”；回收已不存在，因此该项置 `disable`（默认关闭、不实现）。保活仍由 24h 回写与显式 job 承担。
- 失败/stale 才手动重启；禁止任何后台自动 stale 复活或自动 rebind。

### 3.3 wire / transport / CDP

- 直接采用上游 wire 索引与解析修复；不再保留 fork 的重复补丁分支。
- F-01（缺 thoughtSignature 兜底）与 F-04（functionResponse 强制 Struct）仍作为 PG 验收项，但实现优先使用上游原生路径；测试证明，不重复造轮子。
- 空 200、malformed、页面关闭统一归 `BrowserTransportError`；不得伪装成 200 SSE。
- gzip 只影响传输编码，不改变 capture/replay 与事件账本。

### 3.4 config 新键

- `rotator`、`paid_model_rules`、`tier_quotas`、`account_presets` 纳入 `config.yaml` / `defaults/config.yaml` 单一 schema；实例级仍只有 `count/surface/min_tier`。
- 删除 `browser_idle_timeout`；`timeout_replay/timeout_stream` 默认 `600`。
- P9-pre 对缺失/部分 `rotator` 配置 fail-fast；`tier_quotas/paid_model_rules/account_presets` 缺失时按上游默认模板初始化，不静默使用硬编码 fallback。
- `api_keys` 语义按本仓改写：默认拒绝；只有显式空 Key 才开放。上游“无 Key 即开放”不得沿用。

### 3.5 stats 新字段

- `reasoning_tokens`、`cached_tokens`、`finish_reasons` 进入模型统计；`account_breakdown` 是 stats REST 响应的按账号明细，不是新事件类型。
- `cachedContentTokenCount` 进入 Gemini usage 归一化；token 汇总仍为 generation + input + cache，不含 `countTokens`。
- 不新增 feature 行；这些字段归既有 `D10-*` 能力，只有 `12 §4` 的事件字段扩展。

### 3.6 blue-green / rebind

- 上游 `session.py:211` 的 “Blue-Green: always create a dedicated new tab” 与 `_retiring_contexts` 后台 drain 不在本仓采用；它不是单纯的重连优化，而是“先建新页、后 drain 旧页”的双页切换。
- 本仓 rebind 固定为：`READY`（`active_requests==0`）→ `RETIRING`（拒绝新请求）→ 关闭旧 Page/Context → `CLOSED` → 新建 Context+Page → `STARTING` → `READY`。
- 同一账号任何时刻最多 1 Page；不得为切号预先建第二个 Page，不得保留后台 drain 任务或 `_retiring_contexts`，不得借 blue-green 实现自动 rebind。
- 可保留的上游能力：cookie 注入、BotGuard bootstrap、模板捕获、`ensure_ready` 就绪门、关闭时的 drain 等待语义。drain 只发生在关闭旧 Page/Context 时；不存在“新页先建、旧页后台 drain”的双页窗口。

## 4. 硬约束（本次裁决的边界）

- 请求实例固定 `1 账号 + 1 Context + 1 Page + 1 surface`；同账号 chat/build 不并存。
- `active_requests` 是唯一并发账本；无 lease/reservation/超时预算。
- 消息到达唤醒合格 `SLEEPING/UNSTARTED`；保存消息后允许切换实例再重试。
- 429 先同账号重试，再唤醒/切现有实例，保存后重试，耗尽 `503`；已出正文不切号。
- 无后台自动 stale 复活、无自动 rebind、无自动 rebalance、无 pinned。
- OpenAI Chat 必做；Claude 在 Build 后。
- 不得为了兼容旧测试复活 lease/pinned/多页。

## 5. 测试基线关系（修正旧记录）

- 主 `rebuild`：`tests/**/*.py` 共 80 个；`06-test-disposition.csv` 原有 80 行，覆盖 80/80。
- `upstream/master @ 99c38ba`：`tests/**/*.py` 共 28 个（含 `tests/conftest.py`，测试模块 27 个），不是 26。
- “26” 对应 `296680f/b8af406` 的测试文件数；`99c38ba` 新增 `test_account_presets.py`、`test_tier_preemption.py` 后为 28。worktree `13` 记录中的 “26/26” 是旧计数且漏报 2 个新文件，本文件更正。
- 本次向 `06-test-disposition.csv` 追加 2 行（`test_account_presets.py`、`test_tier_preemption.py`），使表为 82 行：主 `rebuild` 仍 80/80，`99c38ba` 28/28。
- `26/26` 不等于 `80/80`：前者是旧上游覆盖口径，后者是 fork 旧测试全量；两者不能互相冒充。
- feature 集不变（518 行），只按本裁决调整 7 行的 decision/note：`D01-012`、`D01-038`、`D03-035`、`D04-042` → `drop`；`D03-004`、`D03-009`、`D08-022` → `rewrite`（分别去掉 pipe watchdog、liveness checker/轮询、idle 回收，保留进程组/Job Object/atexit、页面失败即失败、消息唤醒 SLEEPING/UNSTARTED）。CSV 与 ledger 已同步，索引脚本 `errors=0`。

## 6. 结论

`ready for independent review`。

- 文档层面无未裁决的上游冲突；上述 6 类冲突均已给出 adopt/rewrite/disable/keep。
- 不宣告 handoff：Gate 0.1 需先提交本轮文档；Gate 0.5 必须在迁移 worktree 基于 `99c38ba` 重新验证（config/event schema、fixture、acceptance、28/28 测试覆盖）。
- 进入 P9-pre 前仍须独立 reviewer 复核本文件、`05/06/10/12/13` 的一致性。
