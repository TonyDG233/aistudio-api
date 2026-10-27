# 08-06 Final Readiness Audit

> 本文件取代 `08-05-final-readiness-audit.md` 作为最终 handoff 门；`10-handoff-prompt.md` 的 Gate 0 完整条件以本文 §6 为准。`08-05` 保留为历史审计，不回改原文。

- 审计对象：`rebuild` @ `0f9357b0e18b3c727dbe4258706e8d05e7265c3d`（审计开始时 `git status --porcelain` 为空），`upstream/master` @ `4623109b97d14db30e304005fe1d52c29a46a7c2`。
- 审计基线 SHA：`0f9357b`。本文件只记录审计基线；提交本文件后的交接 SHA 由新 agent 按 `10:25` 用 `git rev-parse HEAD` 现查，不写回 `10`。
- 交接基线 SHA：`7cd92f7dcb2b8675050aa342a38b9853a27e33c4`（rebuild，本轮文档修复提交；`git rev-parse HEAD` 于该提交后现查）。其后仅追加本记录与 changelog，内容基线仍为该 SHA。
- 输入：`05-restart-map.md`、`10-handoff-prompt.md`、`08-05-final-readiness-audit.md`、`06-index-validation.md`、`06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`、`06-test-disposition.csv`、`12-gate0-inputs.md`、`docs/dev/evidence/INDEX.md`、`.gitignore`、`docs/changelog.md`。
- 方法：只读 git 元数据/文档/CSV/ledger；原样运行 `06-index-validation.md:20-127` 的脚本；复核 08-05 的 R1/R3/R4/C1/C2/C3；实测 11 个 ignored `.log` 的 ignore/tracked 状态与内容扫描；统计测试处置覆盖；核对 D12/状态机/证据 allowlist。未修改代码/测试/配置，未提交/推送/合并，未联网，未访问真实账号/IP/Google。
- 本文件新增时为 untracked；必须提交后才能满足 G0.1/G0.9（`08-05:138,146`、`05:96`、`10:8`）。

## 1. 结论

**可 handoff（有条件）：08-05 的 3 个剩余项均已实质闭合，无业务代码/索引/状态机/证据 allowlist 阻塞；唯一收尾是把本文件提交、定稿 `docs/changelog.md` 的 08-06 验证行，并确认工作区干净。**

- 08-05 的 C1（ignored logs）已闭合：`$evidenceLogs` 与 `$ignoredLogs` 均为 11 项，`$evidence` 已不含 `.log`，日志用 `Copy-Item -Force` + `git add -f` 迁移（`05:139-151`、`05:211-233`、`05:236-254`）。
- 08-05 的 C2（restore 循环）已闭合：restore 循环只遍历 `$evidence`，不再包含未跟踪日志；迁移后存在性检查已加入 `08-06`（`05:231-233`、`05:256-262`）。
- 08-05 的 C3（提交/changelog）已闭合主体：`08-05` 已随 `0f9357b` 提交；changelog 已记录 `08-01..08-06`（`docs/changelog.md:22-25`）。残留仅 changelog 的验证行仍写“待 08-06 复核”，且未记录基线 SHA（需在提交本文件时定稿）。
- 索引校验 `errors=0`；D12 决策、状态机、证据 allowlist 无阻断性矛盾；Gate0.5 未创建只阻断 P9-pre，不阻断 handoff（`12:133-142`、`12:144-150`、`10:40`）。
- `10` 已引用 `08-06`，且明确只有 `08-06` 结论为“可 handoff”后才使用（`10:19`、`10:22`、`10:24`）；未把 SHA 写回 `10`（`10:8`），未发现旧基线 `7a6e69a` 或把旧审计当最终裁判的引用。

## 2. 剩余项表

| 08-05 项 | 当前状态 | 证据 | 残留 |
|---|---|---|---|
| R1 / C1：`*.log` 迁移与扫描 | **闭合** | `$ignoredLogs` 11 项（`05:139-151`）；`$evidence` 无 `.log`（`05:211-230`）；`$evidenceLogs` 11 项（`05:236-248`）；`Copy-Item -LiteralPath ... -Force` + `git add -f`（`05:249-254`）；实测 ignored 清单恰好这 11 个：p2-verify 3、p3-build3 3、wp5-20261018 5 | 无 |
| C2：restore 循环必然报错 | **闭合** | restore 只遍历 `$evidence`（`05:231-233`），11 个日志改走独立 copy/force-add 循环（`05:249-254`）；`$mustExist` 含 `$evidence + $evidenceLogs + 08-06`（`05:256-262`） | 无 |
| C3：08-05 未提交 + changelog 未记录 | **主体闭合** | `git ls-files` 显示 08-03/04/05 均已跟踪；HEAD `0f9357b` 含 08-05；changelog 已列 `08-01..08-06`（`docs/changelog.md:22-25`） | `docs/changelog.md:24` 仍写“待 08-06 复核”；changelog 尚无基线 SHA；本文件尚未提交 |
| R3：10 引用最终审计 | **闭合** | `10:19`（必读第 8 项）、`10:22`（交接门）、`10:24`（Gate0 完整条件）；08-01/08-02 降为历史（`10:18`） | 08-05 顶部仍自称最终门，需以本文件取代（见 §3） |
| R4：SHA 自引用 | **闭合** | `05:96` 与 `10:8` 均只记录到最终复审/changelog，10 保持“当前 HEAD + 现查”；本文件只记基线 `0f9357b` | 交接 SHA 需新 agent 现查，不能把基线当最终 SHA |
| 08-04 R2/R5/R6/R7 | **仍闭合** | D12-004/006/008 = port（`06-feature-index.csv:486,488,490`）；BUSY→READY，`active_requests=0` 后才 RETIRING（`12:50,57,60`）；Gate0.5 路径明确（`12:133-142,144-150`）；07 为历史快照 | D12-006/008 note 有措辞残留（§3） |
| 索引一致性 | **通过** | 实测 `OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0`（脚本 `06-index-validation.md:20-127`） | 无 |
| 测试处置覆盖 | **通过** | `tests/**/*.py` 80、CSV 80、missing 0；keep 38 / rewrite 37 / delete 5；pending 0（`06-test-disposition.csv:1-81`） | 无 |
| Gate0.5 工件 | **设计内未通过** | 5 项未勾（`12:144-150`）；config/event/pg fixture/acceptance README 目标路径（`12:133-142`）；实测均不存在 | 只阻断 P9-pre（`10:40`），不阻断 handoff |
| 10 自足性 | **基本自足** | Gate0（`10:25-32`）、Gate0.5（`10:34-40`）、顺序（`10:42-44`）、状态机（`10:46-55`）、PG（`10:57-59`）、禁止项（`10:61`）、验收（`10:63`）齐备；证据逐路径清单委托 `05 §6.2/6.5`（`10:12`、`10:24`） | `10:29` 的内联存在性清单未列 08-06（`05:257` 已列）；`10:63` 的“08 §验收清单”需本文 §6 固定出处 |
| 08-05 权威声明 | **已被本文取代** | `08-05:3` 仍写“10 的 Gate0 以本文 §6 为准、本文结论为可 handoff 前不得下发”；`08-05:13,206,213` 仍为“不可” | 历史文件未加“已被 08-06 取代”横幅；不影响 10 的必读路径 |

## 3. 仍阻塞

### 3.1 必须完成（机械收尾，不是实质缺陷）

1. **提交本文件，恢复干净树。** 本文件当前 untracked；G0.1/G0.9 要求 `git status --porcelain` 为空且最终审计已提交（`08-05:138,146`、`05:96`、`10:8,25`）。提交后 G0.1/G0.9 即通过。
2. **定稿 changelog 的 08-06 验证行。** `docs/changelog.md:22-25` 已记录 08-03/04/05/06 范围，但 `:24` 仍为“待 08-06 复核”，且没有基线 SHA；`05:96` 与 `10:8` 要求把 SHA 记录到最终复审/changelog。建议改为“已由 08-06 复核：可 handoff；审计基线 `0f9357b`（交接 SHA 现查）”，不要写自身提交 SHA。

### 3.2 非阻断残留（不影响 handoff，可在提交前顺手修）

| 项 | 位置 | 说明 |
|---|---|---|
| 10 的内联迁移存在性清单未含 08-06 | `10:29` | `05:257` 已含；`10:24` 又要求按本文 §6 执行，因此只是重复清单不一致，不是缺口 |
| 08-05 仍自称最终门 | `08-05:3,13,206,213` | 本文已取代；建议在 08-05 顶部加一行历史横幅（本次审计不允许改旧文件） |
| 05 §6.7 提到不存在的“11”文档 | `05:304` | 现有编号为 05/06/07/08/09/10/12；属历史编号残留，不影响执行 |
| D12-008 note 措辞 | `06-feature-index.csv:490` | “no lease/503 old contract” 易被读成 503 也废弃；实际保留 429→503（`12:61`）。建议改为 “no lease; 429→503 retained” |
| D12-006 note 落点 | `06-feature-index.csv:488` | 说 “rewrite build identity tests”，但唯一同名测试是 delete（`06-test-disposition.csv:23`）；实际改写落点为 D07-038（`06-feature-index.csv:342`） |
| 10:63 的 G1-G11 出处 | `10:63` | 只写“08 §验收清单”；本文 §6.3 固定为 `08-05` 附录 A / `08-01:129-140` |

### 3.3 已确认不阻塞

- Gate0.5（config/event/pg fixture/acceptance README）未创建：只阻断 P9-pre（`12:133-142`、`12:144-150`、`10:34-40`），不阻断 handoff。
- 11 个日志扫描命中仅为 header 名列表（如 `authorization` / `x-goog-api-key` 字样），无真实 key/cookie/header 值；`spikeQ4*` 不在迁移数组（`INDEX.md:21-22`、`05:161`），BOTGUARD 按 upstream 原生 grandfather（`05:160`）。
- 旧 WP 正文、`ledger-batches` 批次文件归属仍按 `05:207-208,270-272`；无新增冲突。

## 4. Gate0 通过条件（判定摘要）

| ID | 条件 | 当前判定 | 依据 |
|---|---|---|---|
| G0.1 | 文档（含 08-06）已提交；树干净；05/10 基线一致；SHA 无自引用 | **提交本文件后过**；当前因 08-06 untracked 未过 | `05:96`、`10:8,25` |
| G0.2 | 索引脚本 `errors=0`，518/518，桥接计数正确 | **过** | 实测输出；`06-index-validation.md:20-127` |
| G0.3 | 决策冲突一致；P9-Q3 不 drop 日志 | **过** | 脚本 `EXPECTED_DECISIONS`/`REQUIRED_NOTES`；`06-decision-log.md:378` |
| G0.4 | secret/evidence 只扫迁移集合；11 个 ignored logs 全扫；BOTGUARD/spikeQ4 处理正确 | **过（文档条件）** | `05:108-162`；实测 11 个日志命中仅说明文字 |
| G0.5 | worktree/分支正确；只迁 rebuild 文档 + allowlist；无整树 restore；11 个日志 force-add；无 restore 报错；迁移后关键文件存在 | **做好 commit 后过** | `05:180-288`；`$mustExist` 含 08-06（`05:257`） |
| G0.6 | ledger-batches 仅历史；旧 WP 不入新 worktree；archive/INDEX/changelog 归属正确 | **过** | `05:207-208,270-272` |
| G0.7 | 12/处置表/acceptance 模板已存在并通过评审 | **过（文档级）** | `12:113-150`、`06-test-disposition.csv:1-81`；acceptance README 属 Gate0.5 |
| G0.8 | 10 已更新、可独立执行、不依赖旧 08 行号 | **过** | `10:18-32`；本文 §6 固定条件 |
| G0.9 | 08-06 已提交且 10 引用；10 可独立执行 | **提交本文件后过**；当前 08-06 untracked | `10:19,22,24` |

## 5. 复核方法与实测

```text
git status --porcelain                  -> 空（审计开始）
git rev-parse HEAD                      -> 0f9357b0e18b3c727dbe4258706e8d05e7265c3d
git rev-parse upstream/master           -> 4623109b97d14db30e304005fe1d52c29a46a7c2
08-03/04/05 tracked                     -> 是（08-05 随 0f9357b 提交）
06-index-validation 脚本                -> OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0
tests/**/*.py vs 06-test-disposition    -> 80/80；keep 38 / rewrite 37 / delete 5；pending 0
allowlist ignored files                 -> 11 个 *.log（p2-verify 3、p3-build3 3、wp5-20261018 5）
05 $ignoredLogs                         -> 11 项（05:139-151）
05 $evidenceLogs                        -> 11 项（05:236-248）
05 $evidence 含 .log                    -> 0 项（05:211-230）
Copy-Item + git add -f                  -> 05:249-254
mustExist 含 08-06                      -> 05:257
ignored log secret scan                 -> 仅 header 名列表/说明文字，无真实值
D12-004/006/008                         -> CSV 均 port（486/488/490）
Gate0.5 目标工件                        -> 均不存在（符合 12:133-142 的“待创建”）
10 旧 SHA/旧最终审计引用                -> 无 7a6e69a；08-01/02 仅历史；08-06 为最终
```

## 6. Gate0 完整条件与验收（`10:24` 所指“08-06 §6”）

### 6.1 执行顺序

1. 提交本轮文档：本文件 + `docs/changelog.md` 的 08-06 定稿（记录审计基线 `0f9357b`，不写自身提交 SHA）。
2. 确认 `git status --porcelain` 为空、`git rev-parse HEAD` 与 05/10 的“当前 HEAD”口径一致。
3. 运行 `06-index-validation.md:20-127`，必须得到 `errors=0`。
4. 按 `05 §6.4` 从 `upstream/master` 新建 `.kilo/worktrees/rebuild-upstream`（`rebuild/upstream`），不合并 master/rebuild。
5. 按 `05 §6.5` 迁移：
   - `git -C $new restore --source=rebuild -- docs/dev/rebuild`；
   - 删除 `ledger-batches/*.md`（保留 README，`05:208`）；
   - 对 `$evidence` 中有 tracked 文件的路径（不含 `.log`）逐项 restore（`05:211-233`）；
   - 对 11 个 `.log` 用 `Copy-Item -Force` + `git add -f`（`05:236-254`）；
   - `$mustExist` 必须覆盖 `$evidence + $evidenceLogs + docs/dev/rebuild/08-06-final-readiness-audit.md`（`05:256-262`）。
6. 执行 `git -C $new add -A docs/dev/rebuild docs/dev/evidence docs/changelog.md`、`git -C $new status -uall`，确认无意外 untracked。
7. 验收：
   - `$new` 中 11 个 `.log` 全部存在且被跟踪；
   - `p2-verify/`、`p3-build3/`、`wp5-20261018/` 日志一个不少；
   - 无 `git restore` 报错（restore 路径不含未跟踪日志）；
   - BOTGUARD 未复制/修改；`spikeQ4*` 不在 `$new`；
   - `06-index-validation.md`、`10-handoff-prompt.md`、`08-06-final-readiness-audit.md`、`12-gate0-inputs.md`、`06-test-disposition.csv`、`docs/dev/evidence/INDEX.md`、`docs/changelog.md` 均存在。
8. Gate0.5：按 `12:144-150` 在进入 P9-pre 前勾满；未勾满不得进入 P9-pre（`10:40`）。

### 6.2 secret/evidence 判定

- `git grep` 只扫 `docs/dev/rebuild`（`05:113`）；evidence 按 allowlist 逐路径扫（`05:115-137`）。
- 11 个 ignored `.log` 用 `Select-String` 扫（`05:139-156`）；命中仅限占位符/说明文字/header 名列表。
- `spikeQ4-evidence.json`、`spikeQ4.py` 不入新仓库（`INDEX.md:21-22`、`05:161`）；BOTGUARD 为 upstream 原生 grandfather，不复制/不改/不计失败（`05:160`）。
- `docs/changelog.md` 属 allowlist，随 Gate0 迁移（`INDEX.md:37`、`05:229`）。

### 6.3 G1-G11 出处

`10:63` 的 G1-G11 验收清单固定在 `08-05-final-readiness-audit.md` 附录 A（源自 `08-01-execution-readiness-audit.md:129-140`）；执行时不要把“08 §”理解为旧 08-02/08-03 的行号。

## 7. handoff 判定

| 项目 | 判定 |
|---|---|
| 10 是否引用最终审计 | 是（`10:19`、`:22`、`:24`） |
| 是否只在 08-06 结论为可 handoff 后使用 | 是（`10:22`）；本文结论为“可 handoff（有条件）” |
| SHA 是否写回 10 | 否（`05:96`、`10:8`）；只记录基线，交接 SHA 现查 |
| 10 是否自足 | 基本自足；Gate0 完整条件通过 `10:24` 指向本文 §6，证据路径委托 `05 §6.2/6.5`（05 是必读第 1 项，`10:12`） |
| 是否还有旧 SHA/旧最终审计引用 | 无旧基线 `7a6e69a`；08-01/08-02 已标历史（`10:18`）；08-06 为唯一最终门 |
| 是否可立即下发 | **提交本文件 + 定稿 changelog 后即可**；当前仅因 08-06 untracked/changelog 验证行未定稿而不满足 G0.1/G0.9 |
| 下发后首步 | 新 agent 按 §6 执行 G0.1-G0.9，再按 `12 §6` 勾选 Gate0.5；未过不得进入 P9-pre |
| Gate0.5 是否阻断 handoff | 否；只阻断 P9-pre（`12:133-142`、`12:144-150`、`10:40`） |
