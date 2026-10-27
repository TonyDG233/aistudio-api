# 08-05 Final Readiness Audit

> 历史快照：本文件是修复过程中的复审报告；最终 handoff 门以 `08-06-final-readiness-audit.md` 为准。本文不改写历史结论。

- 审计对象：`rebuild` @ `7a6e69a15fe82409c749c818c2381784373fc9dc`（审计开始时 `git status --porcelain` 为空），`upstream/master` @ `4623109`。
- 审计基线 SHA：`7a6e69a15fe82409c749c818c2381784373fc9dc`。交接时的 HEAD 以 `git rev-parse HEAD` 现查；本文不记录自身提交的 SHA（避免自引用）。
- 输入：`05-restart-map.md`、`10-handoff-prompt.md`、`08-04-final-readiness-audit.md`、`06-index-validation.md`、`06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`、`06-test-disposition.csv`、`12-gate0-inputs.md`、`07-architecture-lifecycle.md`、`docs/dev/evidence/INDEX.md`、`.gitignore`、`docs/changelog.md`。
- 方法：只读文档/CSV/ledger/git 元数据；运行 `06-index-validation.md` 原样脚本；复核 08-04 的 R1/R3/R4 及 R2/R5/R6/R7；逐路径核对 evidence allowlist 的 commit 树/工作区/忽略状态；实测 `git restore` 对未跟踪 `.log` 的行为；统计处置表覆盖与 feature 引用。未修改代码/测试/配置，未提交/推送/合并，未联网，未访问真实账号/IP/Google。
- 本文件在审计结束时为 untracked；必须先提交（见 §4 C3、§6 G0.1/G0.9），否则 Gate 0 与 10 的交接门都不成立。

## 1. 结论

**不可 handoff（差 3 项 Gate0 文档收尾；修完即可 handoff，无需改业务代码）。** 10 的结构性问题（R3/R4）已闭合：10 已引用 `08-05`，05/10 不再要求把 SHA 写回 10；08-04 的 R2/R5/R6/R7 仍闭合，索引校验 `errors=0`。但 R1 只闭合了“3 个 p3-build3 日志”这一例：同一 allowlist 内另有 8 个被 `*.log` 忽略的 evidence 日志既未迁移也未扫描，且 05 的 restore 循环仍会把 3 个未跟踪日志送进 `git restore` 必然报错。完成 §4 的 C1-C3（文档改动 + 提交）后，须按 `05 §6.7` / `10:22` 由独立审计复核，并据实把本行结论改为“可 handoff”后才可下发；§4.2（Gate0.5 工件）不阻断 handoff，只阻断 P9-pre。

| 维度 | 判定 |
|---|---|
| R1 evidence 迁移/扫描 | **部分闭合**。3 个 p3-build3 `.log` 已有 Copy-Item + `git add -f` + Select-String 兜底（`05:233-244`、`05:141-151`）；但 `p2-verify` 3 个、`wp5-20261018` 5 个同类 `.log` 未处理，且 `05:206-231` 的 restore 数组仍包含 3 个未跟踪日志。 |
| R3 审计闭环 | **闭合**。10 已把 `08-05` 写入必读与交接门（`10:19`、`:22`、`:24`），不再指向 08-02/08-03 的旧结论；残留仅为 08-05 尚未提交。 |
| R4 SHA 自引用 | **闭合（口径统一）**。`05:96` 不再要求写入 10，`10:8` 明确“不写回本行”；SHA 记录到最终复审/changelog。残留：只能记录被审计基线 SHA，不能记录 08-05 自身提交 SHA。 |
| 08-04 R2/R5/R6/R7 | **仍闭合**。见 §3。 |
| 索引/决策一致性 | **通过**。`errors=0`；无 pending；处置表 80/80。见 §3.4。 |
| 10 自足性 | **基本自足**。Gate0、Gate0.5、顺序、状态机、PG、证据 allowlist、复审条件齐备；证据逐路径清单与 `.log` 特例委托给 05 §6.2/6.5（05 是必读第 1 项）。见 §7。 |
| 是否可立即下发 | **否**。先完成 §4 C1-C3。 |

## 2. R1/R3/R4 复核表

| ID | 08-04 要求 | `7a6e69a` 现状 | 证据 |
|---|---|---|---|
| R1 | `*.log` 用 Copy-Item + force-add；tracked allowlist 用 restore；secret scan 同时覆盖 git 与 ignored logs；INDEX 17-34 与迁移清单一一对应；spikeQ4/BOTGUARD 排除闭合 | **部分闭合**。① `05:233-244` 对 3 个 p3-build3 `.log` 使用 `Copy-Item -Force` + `git add -f`，`05:141-151` 用 Select-String 补扫；② 但 `p2-verify` 3 个与 `wp5-20261018` 5 个同名 `.log` 同样被 `.gitignore:35` 忽略、不在 commit 树，目录级 `git restore` 只带 tracked 文件，二者均未迁移、未扫描；③ `05:206-228` 的 `$evidence` 仍含这 3 个未跟踪 `.log`，`05:229-231` 必然 pathspec 报错（实测 exit=1）；④ `$scan`/`$evidence` 两数组各 21 项逐项相同；`INDEX.md:17-34` 的 16 条“是”展开为 18 个路径，加 INDEX/archive/changelog 共 21 项，路径级 1:1 成立；⑤ spikeQ4 不入数组/不入 INDEX（`INDEX.md:21-22`、`05:156`），BOTGUARD 是 upstream 原生且不复制（`05:155`、`.gitignore:17`、`git cat-file -e upstream/master:...`=true）。 | `05:110-151`、`05:190-245`、`05:262-269`、`INDEX.md:12-37`、`.gitignore:35`、`git ls-files --others --ignored --exclude-standard -- docs/dev/evidence`（11 条，仅 3 条被处理）、`git restore --source=HEAD -- .../acceptance-run1.log`（exit=1） |
| R3 | 08-03 已提交且 10 引用；交接条件明确 | **闭合**。10 的必读第 8 项是 `08-05`（`10:19`），交接门改指 `08-05`（`10:22`），Gate0 完整条件改指 `08-05 §6`（`10:24`）；08-01/08-02 明确降为历史只读（`10:18`），不再把 08-02 §6 / 08-03 的旧结论当最终裁判。 | `10:18-19,22,24`、`08-03:3`、`08-04:16,28,38-42` |
| R4 | 避免 SHA 自引用；交接条件明确 | **闭合（口径统一）**。`05:96` 改为“把 SHA 记录到最新最终复审/changelog；10 保持当前 HEAD + 现查，不写回 SHA”；`10:8` 同样只要求记录到 08-05/changelog，并明确禁止写回 10。二者不再互斥。残留：写入 08-05/changelog 的只能是“被审计基线 SHA”，最终提交 SHA 由 `git rev-parse` 现查，否则仍是写→脏→再提交的循环；10:8 的“未提交则从工作区复制”若被当成可跳过 G0.1 的通道，会与“`git status --porcelain` 为空”冲突。 | `05:96`、`10:8`、`10:22`、`10:25` |

结论：R3/R4 可以关闭；R1 必须按 §4 C1 补齐，否则不能宣称 Gate 0 的“evidence 全迁移 + secret scan 覆盖完整”。

## 3. 08-04 其余项与索引校验

### 3.1 R2（D12-004/006/008、处置表）

| 项 | 现状 | 证据 |
|---|---|---|
| D12-004 | CSV/ledger 均 `port`，note 为单 Context/单 Page + `active_requests` 改写 | `06-feature-index.csv:486`、`06-feature-ledger.md:571` |
| D12-006 | CSV/ledger 均 `port`，note 明确 no lease identity | `06-feature-index.csv:488`、`06-feature-ledger.md:573` |
| D12-008 | CSV/ledger 均 `port`，note 明确 capacity 用 `active_requests` | `06-feature-index.csv:490`、`06-feature-ledger.md:575` |
| 处置表 | `test_build_lease_identity.py`=delete、`test_session_blue_green_readiness.py`=delete；`test_browser_session.py`/`test_build_surface.py`/`test_instance_pool.py`/`test_instance_manager.py`/`test_routing_pipeline.py`=rewrite；无 lease keep | `06-test-disposition.csv:22-23,25,43-44,65,68` |
| P10-Q1 | 不再把租约测试点名 keep（`keep/port` 范围表述） | `06-decision-log.md:402` |

非阻断残留（08-04 §3.5 未改）：D12-008 note 的 “no lease/503 old contract” 与保留的“429 耗尽→503”（`05:58`、`12:61`）措辞易误读；D12-006 说 “rewrite build identity tests”，但唯一同名测试文件是 delete，实际落点是 feature `D07-038`（实例 auth/session），不是新测试文件。

### 3.2 R5（BUSY→RETIRING）

状态表 BUSY 行只允许回 `READY`，并明确 `active_requests=0` 后才允许 `RETIRING`（`12:50`）；事件文字与手动 rebind 一致（`12:57`、`12:60`）；接口红线“关闭/改绑前检查 `active_requests`，大于 0 拒绝”（`12:36`）；05/10 同口径（`05:56`、`10:52`）。未再出现“BUSY 时 drain 到 RETIRING”的旧表述。**闭合。**

### 3.3 R6/R7（Gate0.5 路径、07 快照）

- `12:133-142` 已给出 config/event/pg fixture/acceptance README 的确定性目标路径，并标注“待创建，阻塞 Gate0.5 勾选”；`12:144-150` 五项仍全未勾，符合“Gate0.5 不通过”的设计。
- 实测 `config/config.example.yaml`、`config/config.schema.json`、`tests/fixtures/events/event.sample.json`、`tests/fixtures/pg/f01..f04`、`docs/dev/rebuild/acceptance/README.md` 均不存在。**只阻塞 Gate0.5/P9-pre，不阻塞 handoff。**
- `07:3` 已声明“历史快照，基线 `e3a77c2`；仅只读参考，以 06-feature-ledger/06-decision-log 为准”。**闭合。**

### 3.4 06-index-validation 与 CSV/decision log

原样运行 `06-index-validation.md` 的脚本（未改动），输出：

```text
OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0
```

- 518/518、`ledger_line` 全对、feature 文本/顺序/decision 全一致，无 `pending`。
- `EXPECTED_DECISIONS` 覆盖 08-04 点名的全部组，`REQUIRED_NOTES`（D05-026/D05-039/D08-022）全过；P9-Q3 未 drop 日志功能。
- 处置表：`tests/**/*.py` 80 个文件，listed 80，keep 38 / rewrite 37 / delete 5，pending 0；所有 `feature_ids` 均可解析到 CSV。
- 人工复核 `06-decision-log.md:196-203,378` 与 CSV：无硬冲突。`06-decision-log.md:199` 的 D04 集群句采用“drop 枚举 + port 例外”的压缩写法（`D04-037/041/042/052` 等同时出现在两段），逐项读可还原为 CSV 的 `port`，但字面易误读；CSV/ledger 一致且 `errors=0`，故不阻断。

## 4. 仍阻塞

### 4.1 Gate0 必须（下发前完成：文档改动 + 提交）

**C1（高）evidence 日志迁移与扫描不完整。**

`INDEX.md:17` 明确 `p2-verify/` 为 “SSE/log/out 验证输出”，`INDEX.md:25` 的 `wp5-20261018/` 标注为“旧 harness/输出/截图”（服务日志属“输出”）；但 `05:233-244` 的日志循环只列了 3 个 `p3-build3/*.log`，`05:141-151` 的 Select-String 也只补扫这 3 个。实测 allowlist 目录下被忽略且未跟踪的 `.log` 共 11 个：

```text
docs/dev/evidence/p2-verify/service-off.log
docs/dev/evidence/p2-verify/service-prefer.log
docs/dev/evidence/p2-verify/service-required.log
docs/dev/evidence/p3-build3/acceptance-run1.log
docs/dev/evidence/p3-build3/acceptance-run2.log
docs/dev/evidence/p3-build3/service.err.log
docs/dev/evidence/wp5-20261018/service-f-build.log
docs/dev/evidence/wp5-20261018/service-f-quota.log
docs/dev/evidence/wp5-20261018/service-p-chat.log
docs/dev/evidence/wp5-20261018/service-p-guard.log
docs/dev/evidence/wp5-20261018/service-p-guard2.log
```

其中 `p2-verify` 与 `wp5-20261018` 是按目录 restore（`05:209`、`05:217`），`git restore` 只带 tracked 文件，这 8 个日志不会进入新 worktree，也没有存在性检查会发现它们（`05:262-269` 只查 6 个关键文档）。这些日志与同名 `.out` 内容不同（例：`service-off.log` 3398 B vs `service-off.out` 292 B），不是可丢弃的重复件。

安全影响：实测这 8 个日志未命中真实凭据形状（仅 `service-f-build.log:37` 的 header 名列表），因此不是泄漏事件；但“secret scan 覆盖 ignored logs”的声明对全集合不成立。

最小修复（二选一，建议 a）：
- (a) 把 `05:233-244` 的 `$evidenceLogs` 扩为上述 11 项，并在 `05:141-151` 的 `$ignoredLogs` 同步扩为 11 项；`05:262-269` 的存在性检查加入 evidence allowlist 展开后的路径（至少 11 个日志）。
- (b) 若确认这 8 个日志不迁移，则在 `INDEX.md` 对应行显式写“log 不迁移，仅 `.sse`/`.out`”，并同步 05 的目录 restore 说明；不得保持“目录含 log”却静默丢失。

**C2（中）restore 循环仍会必然报错。**

`05:206-228` 的 `$evidence` 同时包含 3 个未跟踪 `.log`，`05:229-231` 对 21 项逐项执行 `git -C $new restore --source=rebuild -- $item`。对未跟踪路径，`git restore` 返回 `pathspec ... did not match any file(s) known to git`（实测 exit=1）。PowerShell 默认继续执行，随后 `Copy-Item` 能补上，因此不是数据丢失，但会产生 Gate0 失败噪声，且在 `$ErrorActionPreference='Stop'` 下会中断。

最小修复：把 3 个日志从 `$evidence` 移出，只保留在 `$evidenceLogs` 循环；或在 restore 循环内显式过滤 `Test-Path`/`git cat-file -e` 后再 restore。同时把 `08-05-final-readiness-audit.md` 加入 `05:262-269`（以及 `10:29`）的迁移后存在性检查——当前两处清单都不含最终审计本身。

**C3（低）08-05 未提交 + changelog 未记录。**

- 08-05 当前为 untracked；不提交则 `git status --porcelain` 非空，G0.1 与 G0.9 均不通过；且 `restore --source=rebuild -- docs/dev/rebuild` 不会把 08-05 带进新 worktree。
- `docs/changelog.md:15-20` 仍只有 05/06/10/12 的第二轮条目，未记录 08-03/08-04/08-05（08-04 §3.4 的 B-4 未闭合）。`05:96`/`10:8` 要求把 SHA 记入最终复审/changelog，审计链目前缺这一环。
- 最小修复：新增一条日期/范围/变更/原因/验证记录（不写自身提交 SHA；可写本次审计基线 `7a6e69a`），与 08-05 一起提交。

### 4.2 Gate0.5 / 实现阶段可补（不阻断 handoff）

- `config/config.example.yaml`、`config/config.schema.json`、`tests/fixtures/events/event.sample.json`、`tests/fixtures/pg/f01..f04`、`docs/dev/rebuild/acceptance/README.md` 待创建（`12:133-142`）；`12:144-150` 未勾满前不得进入 P9-pre（`05:282`、`10:40`）。
- D12-008 的 “no lease/503 old contract” 与保留的 429→503 措辞建议改为 “no lease; 429→503 retained”；D12-006 建议在 note 中写明改写落点 `D07-038`。
- `05:286` 的复审范围写了不存在的 “11” 文档（现有编号为 05/06/07/08/09/10/12）；属历史编号残留，不阻断。
- `10:63` 的 “G1-G11 见 08 §验收清单” 未给文件名/章节；G1-G11 只定义在历史审计 `08-01:129-140`。本文件附录 A 固定出处，使该句可解析。

## 5. 非阻断残留汇总

| 项 | 影响 | 处置建议 |
|---|---|---|
| `10:8` 的 SHA 记录仍指向 08-05/changelog | 若记录的是“最终提交 SHA”会再次自引用 | 只记录被审计基线 SHA；最终 SHA 现查 |
| `10:8` 的“未提交则工作区复制” | 与 G0.1 干净树互斥，可能被误读为可越过 G0.1 | 以 G0.1 为准；08-05 必须提交 |
| `10:31` G0.7 把 Gate0.5 的 acceptance 模板列为 Gate0 项 | 字面可能被读成 `acceptance/README.md` 必须在 Gate0 存在 | 按 `12 §5` 的模板表判定；README 属 Gate0.5（`12:142`） |
| `05:206-231` 未区分子集 | 3 个日志被 restore 循环重复处理 | 见 C2 |
| `05:262-269`、`10:29` 的存在性检查不含 08-05 | 最终审计可能未进入新 worktree 而不被发现 | 见 C2 |
| `06-decision-log.md:199` 的 D04 压缩写法 | 可能误读 D04-037/041/042/052 为 drop | 以 CSV/ledger 为准；可在后续修订拆句 |

## 6. Gate0 命令与验收（完整条件；10:24 所指）

以下为可机械执行的 Gate0 清单，ID 与 `10:25-32` 的内联 G0.1-G0.8 对齐，并补 G0.9。所有命令从仓库根执行；`$new = "$repo\.kilo\worktrees\rebuild-upstream"`。secret 正则不复制到本文件，统一使用 `05:113`（git grep）与 `05:149`（Select-String）中的同一正则，避免本文自命中。

| ID | 通过条件 | 状态（`7a6e69a`） |
|---|---|---|
| G0.1 | 文档（含 08-05）已提交；`git status --porcelain` 为空；05/10 基线为“当前 HEAD”；SHA 记录无自引用 | **未过**：08-05 untracked。提交后过。 |
| G0.2 | 索引脚本 `OK ... errors=0` | **过**：实测 518/518、errors=0。 |
| G0.3 | 决策冲突项在 CSV/ledger/decision log 一致；P9-Q3 不 drop 日志 | **过**：脚本 `EXPECTED_DECISIONS`/`REQUIRED_NOTES` 全过；人工复核 `06-decision-log.md:196-203,378`。 |
| G0.4 | secret/evidence 只扫迁移集合；命中仅说明文字；BOTGUARD 不复制；spikeQ4 不入库；**ignored logs 全部补扫** | **未过**：tracked 部分过；8 个 allowlist 内 ignored 日志未扫（C1）。 |
| G0.5 | worktree/分支正确；只迁 `docs/dev/rebuild` + allowlist；无 `-- docs`/src/tests 整树；迁移后关键文件存在；**11 个日志已 force-add 且无 restore 报错** | **未过**：3 个 restore 必然报错；8 个日志未迁移（C1/C2）。 |
| G0.6 | ledger-batches 仅历史输入；旧 WP 正文不入新 worktree；archive/INDEX/changelog 归属正确 | **过**：`05:202-203`、`05:252-254`。 |
| G0.7 | 12/处置表/acceptance 模板已存在并通过评审 | **过（文档级）**：12 五节、处置表 80/80、模板在 `12 §5`；`acceptance/README.md` 属 Gate0.5。 |
| G0.8 | 10 已更新、可独立执行、不依赖旧 08 行号 | **过**：10 必读/门/§6 均已改指；G1-G11 见附录 A。 |
| G0.9 | 08-05 已提交且 10 引用；10 可独立执行 | **未过**：08-05 untracked。提交后过。 |

### 6.1 命令

```powershell
# G0.1 基线
git status --porcelain
git rev-parse HEAD
git rev-parse upstream/master          # 期望 4623109...

# G0.2 索引（脚本见 06-index-validation.md:20-127）
python $env:TEMP\kilo_validate_0805.py
# 期望：OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0

# G0.3 决策：由上述脚本覆盖；人工复核 06-decision-log.md:196-203,378

# G0.4 secret/evidence（正则用 05:113/05:149 原文）
$pattern = '<05:113 的正则>'
git grep -n -I -E $pattern -- docs/dev/rebuild
foreach ($f in $allowlistFiles) { Select-String -LiteralPath $f -Pattern $pattern }
# $allowlistFiles 必须含 21 个 allowlist 路径展开后的全部文件，含 11 个 *.log

# G0.5 worktree 与迁移
$repo = (git rev-parse --show-toplevel)
$new  = "$repo\.kilo\worktrees\rebuild-upstream"
git -C $repo worktree list
Test-Path $new                          # 必须 False，已存在则停止
git -C $repo worktree add .kilo/worktrees/rebuild-upstream -b rebuild/upstream upstream/master

git -C $new restore --source=rebuild -- docs/dev/rebuild
Remove-Item "$new\docs\dev\rebuild\ledger-batches\*.md" -Exclude README.md -Force -ErrorAction SilentlyContinue
foreach ($item in $trackedEvidence) { git -C $new restore --source=rebuild -- $item }
# $trackedEvidence = 21 个 allowlist 路径去掉 11 个 *.log 后的 tracked 部分
foreach ($item in $evidenceLogs11) {
  $dest = Join-Path $new $item
  New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
  Copy-Item -LiteralPath $item -Destination $dest -Force
  git -C $new add -f -- $item
}
git -C $new add -A docs/dev/rebuild docs/dev/evidence docs/changelog.md
git -C $new status -uall
git -C $new diff --cached --stat -- docs/dev/rebuild docs/dev/evidence docs/changelog.md
```

验收（必须逐条为真）：

1. `git status --porcelain` 为空（在提交 08-05 之后），`git rev-parse HEAD` = 交接基线。
2. 索引脚本输出 `errors=0`，且 518/518、桥接 31/43/97/44/70/25/67/42/62/37。
3. `git grep` 与 Select-String 对迁移集合的命中仅为占位符/说明文字；11 个 `.log` 全部被扫描。
4. `git -C $new ls-files docs/dev/evidence` 包含 11 个 `.log`；21 个 allowlist 条目在 `$new` 全部存在，且 `p2-verify/`、`p3-build3/`、`wp5-20261018/` 下的日志一个不少；`status -uall` 无意外 untracked（除预期 staged）。
5. 无 `git restore` 报错；BOTGUARD 未被复制/修改；spikeQ4 两个文件不在 `$new`。
6. `$new` 中 `06-index-validation.md`、`10-handoff-prompt.md`、`08-05-final-readiness-audit.md`、`12-gate0-inputs.md`、`06-test-disposition.csv`、`docs/dev/evidence/INDEX.md`、`docs/changelog.md` 均存在。

Gate0.5（P9-pre 前）：按 `12:144-150` 勾选五项；config/event/pg fixture 与 acceptance README 创建前不得进入 P9-pre（`10:40`）。

## 7. Handoff 可用性

| 项目 | 判定 |
|---|---|
| 10 是否引用最终审计 | 是（`10:19`、`:22`、`:24`） |
| 是否只在 08-05 结论为可 handoff 后使用 | 是（`10:22`）；本文当前结论为“不可”，完成 C1-C3 并由独立审计复核、把 §1 结论改为“可 handoff”后提交，才可下发 |
| SHA 是否避免写回 10 | 是（`05:96`、`10:8`）；只记录基线 SHA，最终 SHA 现查 |
| Gate0 是否可机械执行 | 修完 C1/C2 后是；现状 G0.1/G0.4/G0.5/G0.9 未过 |
| 10 是否自足 | 基本自足：Gate0（25-32）、Gate0.5（34-40）、顺序（42-44）、状态机（46-55）、PG（57-59）、禁止项（61）、验收（63）齐备；证据逐路径清单在 05 §6.2/6.5（必读 #1），`.log` 特例在 05:141-151/233-244 |
| 证据 allowlist 是否自足 | 否（需 05 §6.5 的 21 项清单）；建议把 05 §6.5 的 `$evidence`/`$evidenceLogs` 视为交接执行清单的一部分 |
| G1-G11 是否有定义 | 只在 `08-01:129-140`；本文附录 A 固定出处，10:63 的“08 §验收清单”据此解析 |
| Gate0.5 是否通过 | 否（设计内）；只阻断 P9-pre，不阻断 handoff |
| 是否可立即下发 | **否**。先提交 08-05 + 完成 C1-C3 |
| 下发后首步 | 按 §6 执行 G0.1-G0.9，再按 12 §6 勾选 Gate0.5；未过不得进入 P9-pre |

## 附录 A：G1-G11 验收出处（摘自 `08-01:129-140`，作为 10:63 的固定引用）

| ID | 通过条件 |
|---|---|
| G1 | `docs` 中无真实 key/cookie/auth header；archive/INDEX/changelog 存在；旧 WP 不在活动区 |
| G2 | 目标 seam/状态机/config/event schema 已评审；`ARCHITECTURE.md`/`AGENT.md` 与目标一致 |
| G3 | 旧 942 测试有逐文件 keep/rewrite/delete 处置；新分支基线可解释 |
| G4 | 同账号只有 1 Context + 1 Page；chat/build 不并存；关闭释放；sleep/wake 可枚举 |
| G5 | 休眠/未启动可被消息唤醒；保存消息后切换可重试；429/403/transient 符合 P2；耗尽 503 |
| G6 | OpenAI Chat/Gemini 契约通过；API Key 默认拒绝、显式空 Key 才开放；token 口径一致 |
| G7 | 24h 回写覆盖 chat/build-only/active；保存失败不 mark；fake clock 不等待 |
| G8 | 日志 SSE/1/3/7 天导出；client/attempt 分母分离；token 汇总不含 countTokens |
| G9 | Build 9998/signer fallback、realm 隔离、并发、失败 PNG/HTML 可离线验证 |
| G10 | upstream 原生 UI 未被无必要重写；实例/日志/设置入口符合 P8 |
| G11 | 每片有 acceptance 工件；P10 证据索引可查；真实 E2E 仅后置执行 |

## 附录 B：本次实测（只读）

```text
git status --porcelain                  -> 空（审计开始）
git rev-parse HEAD                      -> 7a6e69a15fe82409c749c818c2381784373fc9dc
git rev-parse upstream/master           -> 4623109b97d14db30e304005fe1d52c29a46a7c2
06-index-validation 脚本                -> OK rows=518 ... total=518 errors=0
tests/**/*.py vs 06-test-disposition    -> 80/80，keep 38 / rewrite 37 / delete 5，pending 0
allowlist ignored files                 -> 11 个 *.log；05 仅处理 3 个
git cat-file -e HEAD:<3 个 p3 .log>     -> false（不在 commit 树）
git restore --source=HEAD -- <p3 .log>  -> exit=1，pathspec 不匹配；工作区文件未变
spikeQ4 排除                            -> 不在 INDEX allowlist/05 数组；仅在 rebuild ref
BOTGUARD                                -> upstream/master 与 HEAD 均存在；05:155 grandfather
Gate0.5 工件                            -> 8 个目标路径均不存在（符合“待创建”）
```

关键行号索引：`05:96,110-151,190-245,262-269,284-288`；`10:8,19,22,24,25-32,34-40,42-44,46-55,57-59,61,63`；`08-04:14-20,26-32,34-65`；`12:50,57,60,133-142,144-150`；`INDEX.md:17-34`；`.gitignore:35`；`changelog.md:15-20`；`06-decision-log.md:199,402`；`06-feature-index.csv:486-490`；`06-test-disposition.csv:22-23,25,43-44,65,68`；`07:3`。
