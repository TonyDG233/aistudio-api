# REV-2.2.0.6 — 429 首击直接退避切号（移除同号二次撞墙）+ 前端「调度重试次数上限」防风控提示只读审查报告

- 步骤：Stage 2.2.0 维护项 — `REV-2.2.0.6`。后端：429 配额限流首击即退避切号，彻底移除同账号二次硬撞重试；前端：`调度重试次数上限` 问号提示（`FieldHelpTip`）与副标题补充「防风控特例说明」，并同步瞬时错误/冷却阶梯文案。
- 审查性质：**100% 独立只读 Reviewer**。不采信 Worker 自填结论，直接依据工作树 `git diff`、源码与实跑门禁逐行复核。
- 权威依据：`AGENT.md` §4/§5/§7/§10；`docs/dev/rebuild/06-decision-log.md`、`08-09-decision-record.md` D-01..D-14。
- 审查日期：2026-11-14。
- **独立审查结论：PASS**（P0 = 0，P1 = 0，P2 = 0；原始 Q-1/Q-2/N-1/N-3 已在 2026-11-14 收口复审中关闭，见 §9；新增 N-5 为受保护历史文档/用户文档的残留陈旧表述，非阻塞）。

> 说明：本轮任务书明确把「429 首击直接切号、不做同号二次撞墙」作为防风控特例，因此实现相对 `06-decision-log.md` D4 / `AGENT.md` §5 的旧「先同账号重试」表述属于**经用户本步授权的有意变更**；旧文档的滞后已在 Q-2 记录，Reviewer 未触碰受保护文档。

## 1. 独立审查结论摘要

- **429 分支已彻底移除同号重试**：`routing_pipeline.py:1002-1016` 在 `isinstance(exc, UsageLimitExceeded)` 且 `has_yielded_data=False` 时，直接 `attempt.client.clear_templates()` 并调用 `_switch_or_raise(..., reason="429", quota_exhausted=True)`，不再读取/递增 `attempt.same_retries`，不再 `context.budget.record_retry("429")`，也不再 `asyncio.sleep(retry_same_delay_sec)`。
- **切号退避与 503 收口正确**：`_switch_or_raise` 在 `_try_rebind` 激活新账号前，对 `reason in ("429","verification_required")` 执行随机切号退避（`routing_pipeline.py:1235-1244`，默认 3.0–5.0s，`_resolve_switch_backoff_range` → `switch_backoff_sec=4.0` 的 ±25%）；预算/候选耗尽时因 `quota_exhausted=True` 抛 `SurfaceSwitchExhaustedError`（503），不回落 429（`:1781-1786`）。
- **冷却阶梯自洽**：`account_rotator.py:317-361` 先自增再判档，实测 1→60s、2→300s 且 `drip_mode=True`、3+→600s；无 `1-2 次 60s / 3-4 次 300s` 之类旧注释残留。
- **前端提示与副标题到位**：`schema.ts:807-817` 的 `rotator.max_retries.explanation` 含完整【防风控特例说明】三段（429/verify、瞬态、403）；`SchedulingCard.vue:1003-1005` 与 `:1338-1340` 分别新增副标题。沙盒实测 4 分辨率下 4 列核心路由网格输入框基线对齐（详见 §4）。
- **测试已同步收口**：`test_routing_pipeline.py`、`test_account_rotation_concurrency.py`、`test_routes_gemini.py` 的 429 断言全部改为「首击即切号、`same_retries==0`、不 sleep」。

## 2. 独立核查的变更范围

`git diff --stat` 实测：

| 文件 | 变更 | 核心内容 |
|---|---|---|
| `src/aistudio_api/application/routing_pipeline.py` | +8 / -28 | 429 分支删除同号重试，改直切 + `clear_templates` + `quota_exhausted=True` |
| `src/aistudio_api/application/account_rotator.py` | +4 / -2 | 冷却阶梯改为 1→60s / 2→300s(+drip) / 3+→600s，并更新注释 |
| `web/src/components/config/schema.ts` | +8 / -8 | `max_retries` 三段防风控特例说明；`retry_same_delay_sec` 改为瞬态语义；冷却文案补「第 N 次」 |
| `web/src/components/settings/SchedulingCard.vue` | +6 | 核心网格与高级冷却网格各新增一条副标题 |
| `src/aistudio_api/static/assets/SettingsView.js` | 构建产物 | 已随源码重新构建，含新副标题文案（实测 `Select-String` 命中） |
| `tests/unit/test_routing_pipeline.py` | +? / -? | 429 用例改为直切语义，新增 `same_retries==0` 断言 |
| `tests/unit/test_account_rotation_concurrency.py` | +25 | 新增 `test_rate_limit_cooldown_escalates_short_drip_extended` |
| `tests/unit/test_routes_gemini.py` | +1 / -1 | 单 Free 槽 429 改绑用例：`free_a_calls == 1` |

## 3. 后端逻辑逐行核查

### 3.1 429 分支（`routing_pipeline.py:1002-1016`）

| 核查点 | 结论 | 证据 |
|---|---|---|
| 彻底移除同号重试 | 通过 | 旧 `attempt.same_retries == 0 and context.total_attempts < context.max_retries` 分支已删除，不再出现 `record_retry("429")` / `return "retry_same"` / `sleep(retry_same_delay_sec)` |
| 已出正文不切号 | 通过 | `:1004-1005` `if has_yielded_data: raise exc` 保持 |
| 切号前清理模板 | 通过 | `:1009` `attempt.client.clear_templates()` |
| 直切并带配额耗尽标记 | 通过 | `:1010-1016` `_switch_or_raise(..., reason="429", quota_exhausted=True)` |
| 3–5s 切号退避 | 通过 | `:1235-1244` 在 `reassign` 前 `await self._switch_backoff(...)`；`:344-350` `_backoff_range_from_seconds(4.0)=(3.0,5.0)` |
| 耗尽 503 不回落 429 | 通过 | `:1781-1786` `quota_exhausted=True` → `SurfaceSwitchExhaustedError`（`domain/errors.py:161` 明确 503） |
| 失败槽即时自愈 | 通过 | `_switch_or_raise` 先 `_try_rebind` 再决定继续/耗尽（`:1753-1780`）；无候选时 `record_budget=False` 仅自愈不续命 |

补充：`context.retry_same` 在成功换绑/继续切号时统一置 `False`（`routing_pipeline.py:1285,1788`）；403 分支的同号重签预算（`:968-1000`）与瞬态/hang 的同号重试预算（`:1028-1059`、`:1070-1108`）**未受影响**，与「仅 429/verify 首击切号」的特例边界一致。

### 3.2 冷却阶梯（`account_rotator.py:317-361`）

- 计数先自增（`:333`）再判档（`:350-358`）：第 1 次 `limit_count=1` → `cd_short`(60)；第 2 次 `drip_mode=False, limit_count=2` → 置 `drip_mode=True` 并取 `cd_drip`(300)；第 3 次 `is_drip=True, limit_count=3` → `cd_drip_ext`(600)；第 4 次起恒 600。
- `effective_cooldown = min(cooldown_duration, max(60, seconds_until_midnight))`（`:360-361`）仅用于跨美西午夜收口，非死逻辑。
- **旧注释核查**：全文件检索未发现 `1-2 次 60s / 3-4 次 300s` 等过时描述；`:348-349` 注释已与实现一致。

## 4. UI 沙盒实测（4 分辨率）

启动方式（完全离线沙盒，满足 `AGENT.md §4.6`）：`uv run python main.py server --port 18093 --ui-debug`。测试结束后已 `playwright_browser_close` 并 `background_process stop`，实测 `PORT 18093 FREE`。

### 4.1 核心 4 列路由网格 bounding box（输入框基线）

| 分辨率 | 网格列模板 | 配额口径(分段) | 全局实例上限 input | 连续切换账号上限 input | 调度重试次数上限 input | 第 4 列副标题 `<p>` | 水平溢出 |
|---|---|---|---|---|---|---|---|
| 1440×900 | `255.5×4` | y=430.4, h=25.8 | y=424.9, h=34 | y=424.4, h=34 | y=424.4, h=34 | y=462.4, h=35.8 | 否 |
| 1024×900 | `319×2` | y=393.3, h=25.8 | y=387.8, h=34 | y=461.0, h=34 | y=461.0, h=34 | y=499.0, h=35.8 | 否 |
| 768×900 | `327×2` | y=393.3, h=25.8 | y=387.8, h=34 | y=461.0, h=34 | y=461.0, h=34 | y=499.0, h=35.8 | 否 |
| 375×812 | `301×1` | y=276.8, h=25.8 | y=345.0, h=34 | y=417.0, h=34 | y=489.0, h=34 | y=527.0, h=35.8 | 否（`scrollWidth=375`） |

**结论：输入框基线对齐成立。** 1440 下三个数值输入框 top 差 ≤0.5px（424.9 / 424.4 / 424.4，属网格列宽 255.5 的亚像素取整），视觉一致；新增副标题位于输入框**下方**，只增加第 4 列内容高度，不向上推挤输入框。1024/768 的 2 列布局中，第二行两输入框 top 完全相等（461.0）。375 单列布局输入框宽度 277，无横向挤压、无溢出。

### 4.2 `?` 问号弹窗（`FieldHelpTip`）

- 桌面 1440：弹窗 `x=1112, y=117.4, w=320, h=293.4`，位于触发按钮上方且完全在视口内；文案包含【防风控特例说明】①②③ 三段（脚本断言 `hasThreeSegments=true`）与「推荐配置: 5」。
- 移动 375：弹窗 `x=79, y=164, w=288, h=311, right=367, bottom=475`，`inViewport=true`，无右侧裁切。
- 高级冷却网格（1440，`xl:grid-cols-5`）实测 5 个输入框 top 均约 424–424.5，新增「瞬态错误原号重试等待（429 直接切号）」副标题未破坏同排对齐。

### 4.3 截图（已 `read` 亲眼核验）

- 桌面（含点开的 `?` 弹窗 + 4 列网格）：`reviews/assets/rev-2.2.0.6-max-retries-tooltip-desktop-1440.png`
- 移动（375 单列 + 弹窗 + 副标题）：`reviews/assets/rev-2.2.0.6-max-retries-mobile-375.png`

视觉检查：桌面端 4 列输入框同一水平线、副标题换行自然、弹窗深色卡片完整；移动端控件全宽、副标题不溢出、弹窗未出屏。控制台错误数 0。

## 5. 问题清单（全量）

### P0（阻塞发布）

无。

### P1

无。

### P2

无。

### Q（质量/风险，非阻塞）

- **Q-1 旧注释/文档字符串滞后（已关闭，见 §9.1）**：
  - `src/aistudio_api/application/routing_pipeline.py:10-12` 已改为「429 immediately records model cooldown and switches to the next healthy LRU account after switch backoff (no same-account retry)…」；
  - `src/aistudio_api/infrastructure/gateway/model_defaults.py:1415-1416` 已改为「Normalize the transient-error same-account retry delay (in seconds).」；
  - `defaults/config.yaml:118-120` 已改为「上游瞬态错误（204/404/408/5xx）同账号原位重试前的退避秒数…；注：429 配额限流不做同账号重试，首击直接走 switch_backoff_sec 退避切号」。
- **Q-2 权威文档口径偏差（AGENT.md 已关闭；06 历史记录残留，见 §9.2 与 N-5）**：
  - `AGENT.md:277` 已改为「不做同账号二次撞墙重试 → 首击立即记录模型级冷却并经切号退避等待后…耗尽 503」，与实现一致；
  - `docs/dev/rebuild/06-decision-log.md:52`（D4）与 `:164`（D05-026）仍保留旧「先同账号重试」表述；该文件属受保护决策日志，收口 Worker 依纪律未改，已登记为 N-5 历史残留。

### N（细节，非阻塞）

- **N-1 冷却阶梯语义措辞（已关闭，见 §9.3）**：`web/src/components/config/schema.ts:835` 已改为「连续第 2 次触发 429（或已进入递增限流模式）」，`:846` 已改为「连续第 3 次及以后触发 429」，与 `account_rotator.py:299-300`（非 drip 态成功归零）和 `:350-358`（先自增再判档）的「连续第 N 次」语义一致；`:824` 保留「第 1 次」合理。
- **N-2 模板清理重复调用（无害）**：`routing_pipeline.py:1009` 与 `_try_rebind` 内 `:1287` 各清理一次；重复调用无副作用，可保留。
- **N-3 高级网格副标题换行孤字（已关闭，见 §9.4）**：`web/src/components/settings/SchedulingCard.vue:1338-1340` 文案已缩短为「瞬态错误原号重试等待」；构建产物已重建（旧串「瞬态错误原号重试等待（429 直接切号）」检索不存在），不再断行。
- **N-4 亚像素基线差**：1440 下第 2 列输入框 top 比第 3/4 列高 0.5px（`424.9` vs `424.4`），源于 255.5px 列宽的取整，肉眼不可辨，无需处理。
- **N-5 其他用户文档/规格仍描述旧 429 策略（非阻塞，文档任务）**：`README.md:484`、`README_EN.md:55`、`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:535,985` 仍写「429 同账号重试/首次退避后再切号」；`docs/changelog.md:162` 属历史发布记录，保持不改。建议后续文档任务统一为「429 首击直接切号」。

> 已关闭项：任务书点名的「`account_rotator.py` 是否残留 `1-2 次 60s, 3-4 次 300s` 旧注释」——**经全文件检索确认不存在**，`:348-349` 注释已更新为新阶梯。

## 6. 门禁命令实跑结果（Windows 11 + PowerShell 7）

| # | 命令 | 实际结果 | 退出码 |
|---|---|---|---|
| 1 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync ruff check .` | `All checks passed!` | 0 |
| 2 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations` | 0 |
| 3 | `uv run pytest tests/unit/test_routing_pipeline.py tests/unit/test_account_rotation_concurrency.py tests/unit/test_routes_gemini.py -q` | `95 passed in 1.39s` | 0 |
| 4 | `bun run lint`（`workdir=web`） | `eslint src --max-warnings 0`，无告警 | 0 |
| 5 | `bun run type-check`（`workdir=web`） | `vue-tsc -b`，无错误 | 0 |
| 6 | `bun test`（`workdir=web`） | `97 pass / 0 fail / 829 expect()`（11 files） | 0 |
| 7 | `git diff --check` | 无输出（无空白错误） | 0 |
| 8（加测） | 全量 `uv run pytest -q` | `1665 passed in 48.51s` | 0 |

> 第 8 行为 Reviewer 额外加跑的全量回归，用于确认 429 行为变更未波及其他协议/重试用例（`test_generation_routing`、`test_browserless_auth_rebind`、`test_403_auth_refresh` 等均含 `retry_same` 断言）。

## 7. 沙盒关闭与只读声明

- **沙盒关闭确认**：`background_process stop` 已停止 `--ui-debug` 服务；`Get-NetTCPConnection -LocalPort 18093 -State Listen` 复查输出 `PORT 18093 FREE`；`playwright_browser_close` 已关闭浏览器。
- **100% 只读声明**：
  - 本轮**未修改**任何业务代码、测试、配置或前端源码；`git status` 中所有源码改动均为既有未提交的工作树改动，Reviewer 未新增任何源码编辑。
  - 本轮**唯一写入**：本报告、两张实测截图、`P2-routing/index.md` 与 `domains/INDEX.md` 的索引/看板更新。
  - **未执行** `git commit/push/checkout/reset/add`；**未连接**真实 Google 账号、未拉起真实浏览器/真实上游（仅 `--ui-debug` 纯离线沙盒）。
  - 门禁与 bounding box 数据均为上述命令/脚本实跑输出，未预填、未伪造；本报告不含本机绝对路径。

## 8. 建议的后续动作

1. **Q-1（已完成）**：`routing_pipeline.py:10-12`、`model_defaults.py:1415-1416`、`defaults/config.yaml:118-120` 已同步「瞬态同号重试 / 429 首击切号」新语义，见 §9.1。
2. **Q-2（AGENT.md 已完成）**：`AGENT.md:277` 已同步；`06-decision-log.md` D4/D05-026 属受保护历史决策日志，保留为 N-5，建议获用户授权后补一条决策变更记录。
3. **N-1/N-3（已完成）**：冷却文案已改为「连续第 N 次」，高级网格副标题已缩短，见 §9.3/§9.4。
4. **N-5**：后续文档任务同步 `README.md`、`README_EN.md`、`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md` 的旧 429 策略表述。

## 9. 收口复审（2026-11-14，独立只读复核）

本轮由独立 Reviewer 亲自核对收口 Worker 改动，未采信其自述；改动集中在注释/docstring/前端文案与构建产物，**无 429 分支、冷却阶梯或测试断言变更**。

### 9.1 Q-1 关闭

| 位置 | 收口后内容 | 结论 |
|---|---|---|
| `routing_pipeline.py:10-12` | “429 immediately records model cooldown and switches to the next healthy LRU account after switch backoff (no same-account retry), then finally returns a 503 (never 429);” | 通过 |
| `model_defaults.py:1415-1416` | “Normalize the transient-error same-account retry delay (in seconds).” | 通过 |
| `defaults/config.yaml:118-120` | “上游瞬态错误（204/404/408/5xx）同账号原位重试前的退避秒数…；注：429 配额限流不做同账号重试，首击直接走 switch_backoff_sec 退避切号” | 通过 |

### 9.2 Q-2 关闭（AGENT.md）/ 06 历史残留

- `AGENT.md:277` 已同步为「不做同账号二次撞墙重试 → 首击立即记录模型级冷却并经切号退避等待后…耗尽 503 + 等待/管理页提示；已出正文不切号」，与 `routing_pipeline.py` 实现一致 → **AGENT.md 部分关闭**。
- `06-decision-log.md:52`（D4）/`:164`（D05-026）仍为旧「先同账号重试」。该文件属 `AGENT.md §10` 受保护决策日志，Worker 依纪律未改，Reviewer 亦未修改，登记为 **N-5** 历史残留，留待用户授权的决策记录更新。

### 9.3 N-1 关闭

- `schema.ts:835`：「默认 300 秒；连续第 2 次触发 429（或已进入递增限流模式）时使用…」
- `schema.ts:846`：「默认 600 秒；连续第 3 次及以后触发 429 时使用…」
- 与 `account_rotator.py:299-300`（非 drip 成功归零）和 `:350-358`（先自增再判档）语义一致，已消除「当日第 N 次」歧义。

### 9.4 N-3 关闭

- `SchedulingCard.vue:1338-1340` 副标题由「瞬态错误原号重试等待（429 直接切号）」缩短为「瞬态错误原号重试等待」。
- 构建产物 `src/aistudio_api/static/assets/SettingsView.js` 已重建：检索旧完整串「瞬态错误原号重试等待（429 直接切号）」**不存在**；新串存在。10 个汉字在 11px 字号下宽度 < 列宽 180px，不再出现「号）」孤行。本轮按任务范围未重启 UI 沙盒，此结论由源码宽度与构建产物比对得出。

### 9.5 门禁实跑（本轮独立执行）

| 命令 | 结果 |
|---|---|
| `uv run --no-sync ruff check .` | All checks passed! |
| `uv run --no-sync pyright` | 0 errors, 0 warnings, 0 informations |
| `bun test`（`workdir=web`） | 97 pass / 0 fail / 829 expect()（11 files，154ms） |
| `git diff --check` | 无输出（DIFF-CHECK CLEAN） |

### 9.6 收口后结论

- 原始 **Q-1、N-1、N-3 全部关闭**；**Q-2 在 AGENT.md 层关闭**，`06` 受保护历史记录保留为 **N-5**（非阻塞）。
- 无损复跑说明：本轮 Worker 仅改注释/文案/文档字符串/前端文案与重建静态产物，未触及 429 分支、冷却阶梯或测试断言；上一轮全量 `pytest 1665 passed` 结论仍成立。
- 只读声明（本轮）：Reviewer 仅写入本 REV 报告，未修改任何源码/测试/配置/截图，未执行 git 写操作，未连接真实上游。
- **最终复审结论：PASS**（0 P0 / 0 P1 / 0 P2；N-2/N-4/N-5 为非阻塞残留）。

**独立审查结论：PASS**（0 P0 / 0 P1 / 0 P2；Q-1/Q-2/N-1/N-3 已关闭，N-2/N-4/N-5 为非阻塞残留）。
