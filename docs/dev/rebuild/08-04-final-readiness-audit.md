# 08-04 Final Readiness Audit

- 审计对象：`rebuild` @ `4dfde35`（审计开始时 `git status --porcelain` 为空），`upstream/master` @ `4623109`。
- 输入：`05-restart-map.md`、`06-decision-log.md`、`06-feature-index.csv`、`06-feature-ledger.md`、`06-index-validation.md`、`06-test-disposition.csv`、`10-handoff-prompt.md`、`12-gate0-inputs.md`、`08-01/08-02/08-03`、`09`、`docs/dev/evidence/INDEX.md`、`docs/changelog.md`。
- 方法：只读文档/CSV/ledger/git 元数据；运行 `06-index-validation.md` 校验脚本；复核 `08-03` 的 R1-R7 是否闭合；实测 Gate0 命令的可执行性（含 `gitignore`/commit 树检查）。未修改代码/测试/配置，未提交/推送/合并，未联网，未访问真实账号/IP。
- 性质：本报告是 `08-03` 之后的最终独立复审。除本文件外未新增/改动任何文件；本文件在审计结束时为 untracked，必须在 Gate0 前提交（见 §4 G0.1）。

## 1. 结论

**不可 handoff。** R2/R5/R6/R7 已闭合，R1 只闭合了“清单对应关系”但执行命令仍会失败，R3/R4 未闭合；`10` 的交接门指向已声明为历史快照的 `08-03`，且其字面通过条件无法成立。修完 §3 的 4 项后可转为“可 handoff”，无需改业务代码。

| 维度 | 判定 |
|---|---|
| R1 evidence 迁移与扫描 | **部分**。`05` 的 restore 数组与 INDEX 17-34 已一一对应（21 项完全相同），BOTGUARD/spikeQ4 裁决闭合；但数组内 3 个 `.log` 被 `.gitignore:35` 忽略、不在 commit 树，`git restore` 必然失败、`git add -A` 不会入库、`git grep` 扫不到。 |
| R2 D12 租约测试 | **已解决**。D12-004/006/008 在 CSV/ledger 均为 `port` 且 note 明确 rewrite/no lease；处置表对同名文件为 delete/rewrite，无 lease keep 复活。残留 2 处措辞歧义（非阻断）。 |
| R3 审计闭环 | **未解决**。08-03 已提交且被 10 引用，但 08-03 自述“历史快照、最终以 08-04 为准”（`08-03:3`），结论是“有条件”（`08-03:11`），而 10 仍把 08-03 当“最终复审”并以其“可 handoff”为使用前提（`10:19`、`10:22`）；10 全文不引用 08-04。 |
| R4 SHA 自引用 | **未解决**。`05:96` 仍要求“把 SHA 写入 10-handoff-prompt.md”，`10:8` 明令“不要把 SHA 写回本行”；`10:8` 改为写入 08-03/changelog 仍会在 commit 后再次弄脏工作区，属同一自引用循环。 |
| R5 状态机 | **已解决**。`12:50`/`:57`/`:60` 一致：BUSY 只能回 READY，`active_requests=0` 后才允许 RETIRING；BUSY 时关闭/改绑显式拒绝。与 `12:36`、`05:56` 一致。 |
| R6 Gate0.5 退出项 | **文档已解决**。`12:133-142` 给出 config/event/fixture/acceptance 的确定性目标路径，并明确“待创建，阻塞 Gate0.5 勾选”。文件实际不存在，故 Gate0.5 仍未通过（符合设计）。 |
| R7 07 基线 | **已解决**。`07:3` 已标“历史快照，基线 e3a77c2；仅只读参考”。 |

## 2. R1-R7 复核表

| ID | 08-03 要求 | `4dfde35` 现状 | 证据 |
|---|---|---|---|
| R1 | 05 的 restore 数组与 INDEX 17-34 一一对应；扫描用同一完整 allowlist；BOTGUARD grandfather、spikeQ4 排除闭合 | **清单已闭合，执行未闭合**。`05:195-220` 与 `05:115-137` 为同一 21 项数组（实测逐项相同）；INDEX 17-34 的 16 条“是”展开为 18 个路径，加 INDEX/archive/changelog 共 21 项，无遗漏、无多余；`spikeQ4*` 不在数组（`05:194`）；BOTGUARD 在 upstream 原生树中（`git cat-file -e upstream/master:docs/BOTGUARD_VERIFICATION_CHAIN.md` = true）且不迁移（`05:144`）。**但** `p3-build3/acceptance-run1.log`、`acceptance-run2.log`、`service.err.log` 被 `.gitignore:35` 的 `*.log` 忽略，`git cat-file -e rebuild:<path>` 均为 false，restore 会报 pathspec 不匹配；`git grep` 也扫不到（实测 exit=1，而 `Select-String` 能在文件内命中 `authorization`/`x-goog-api-key` 字样）。 | `INDEX.md:18-20`、`05:115-137`、`05:195-220`、`05:235`、`.gitignore:35`、`git ls-tree -r HEAD -- docs/dev/evidence/p3-build3`（只有 2 个 `.sse` + spikeQ4） |
| R2 | D12-004/006/008 改 port 且 note 明确 rewrite；与处置表 rewrite/delete 一致；无 lease 复活 | **已解决**。CSV `486/488/490` 决定均为 `port`，note 分别为“rewrite for one-context-one-page/active_requests”“rewrite build identity tests; no lease identity”“rewrite capacity tests; active_requests, no lease”；ledger `571/573/575` 同步为 `port`；P10-Q1 不再点名 keep 租约（`06-decision-log.md:402`）。处置表：`test_build_lease_identity.py`=delete（`:23`）、`test_session_blue_green_readiness.py`=delete（`:68`）、`test_browser_session.py`/`test_build_surface.py`/`test_instance_pool.py`/`test_instance_manager.py`/`test_routing_pipeline.py`=rewrite（`:22,25,43,44,65`），与 note 方向一致，无 keep 租约项。残留措辞：D12-008 的“no lease/503 old contract”与 `05:58`/`12:61` 保留的“429 耗尽→503”易被误读为取消 503；D12-006 说“rewrite build identity tests”但唯一同名文件是 delete，未点名改写落点（实际落点见 `csv:342` D07-038“改写为实例 auth/session”）。 | `06-feature-index.csv:486-490`、`06-feature-ledger.md:571-575`、`06-test-disposition.csv:22-23,25,43-44,65,68`、`06-decision-log.md:402` |
| R3 | 08-03 已提交且 10 引用；交接条件明确 | **未解决（门指向错误文档）**。08-03 已随 `4dfde35` 提交；10 在 `:19`/`:22` 引用。但 08-03 顶部已声明“历史快照……最终可用性以 08-04 为准”（`:3`），其自身结论是“有条件可 handoff”（`:11`），并非 10:22 要求的“可 handoff”。10 不引用 08-04，08-04 也未进入必读清单，R3 的缺口在新文档上原样复发。另：10:24 把“完整条件”指向历史文档 `08-02 §6`，而 08-02 的 G0.4/G0.8 与 08-03 §4 的 G0.4/G0.9 语义不同（08-02 G0.4=secret，08-03 G0.4=D12 修复；08-03 G0.9=最终审计已提交），10 内联列表采用 08-02 编号并遗漏 G0.9。 | `08-03:3,11`、`10:19,22,24`、`08-02:196-205`、`08-03:90-98` |
| R4 | 避免 SHA 自引用；交接条件明确 | **未解决（直接矛盾）**。`05:96`：“执行 `git rev-parse HEAD` 并把 SHA 写入 `10-handoff-prompt.md`”；`10:8`：“10 保持‘当前 HEAD + git rev-parse 现查’，不要把 SHA 写回本行（避免自引用）”。二者只能执行一个。`10:8` 让写入 08-03/changelog 也不能消除循环：提交后再写会弄脏工作区（违反 `05:96` 的 clean 检查），写入后再提交又会改变 SHA；而 08-03 已被标为历史快照，不应再追加运行期 SHA。 | `05:96`、`10:8`、`08-03:3` |
| R5 | BUSY 只允许 `active_requests=0` 后 RETIRING；事件文字一致 | **已解决**。状态表 BUSY 行：“`READY`（全部释放）；`active_requests=0` 后才允许 `RETIRING`”（`12:50`）；事件文字：“关闭/改绑在 `BUSY` 时拒绝并提示等待，`active_requests==0` 后才允许 `RETIRING`”（`12:57`）；手动 rebind 只从 READY 归零态进入（`12:60`）；与接口红线 `12:36` 一致。未再出现“BUSY→RETIRING（拒绝新请求）”式 drain 表述。 | `12:36,49-51,57,60` |
| R6 | config/event/fixture/acceptance 有确定性目标路径；“待创建”须明确阻塞 Gate0.5 | **文档已解决**。`12:133-142` 列出 `config/config.example.yaml`、`config/config.schema.json`、`tests/fixtures/events/event.sample.json`、`tests/fixtures/pg/f01..f04`、`docs/dev/rebuild/acceptance/README.md`，标题写明“待创建，不阻塞文档，但阻塞 Gate0.5 勾选”；`12:149` 的 checklist 仍未勾选。实测上述文件当前均不存在，Gate0.5 因此**不通过**（与 `05:258` 一致，不阻断 handoff，只阻断 P9-pre）。 | `12:133-142,144-150`、`05:248-258` |
| R7 | 07 标历史快照 | **已解决**。`07:3`：“历史快照，基线 `rebuild` @ `e3a77c2`；仅只读参考……以 06-feature-ledger/06-decision-log 为准”。 | `07:3` |

## 3. 仍阻塞（下发前修，全部为文档/脚本改动）

### 3.1 B-1（高）10 的交接门指向已作废的结论

`10:22` 规定“本提示词仅在 `08-03-final-readiness-audit.md` 结论为‘可 handoff’后使用”，但 `08-03:11` 的结论是“有条件可 handoff”，`08-03:3` 又声明自己是历史快照、最终以 `08-04` 为准；`10:19` 仍称 08-03 为“最终复审”，全文无 08-04。

后果：全新 agent 按 10 字面执行会判定门未通过而停摆；若忽略该句继续，又等于使用一份未引用最终审计的旧提示词，且 `restore --source=rebuild` 迁移的是 commit 树，08-04 若不提交则新 worktree 里根本没有最终结论。

最小修复：`10:19`/`:22` 改指 `08-04-final-readiness-audit.md`（把 08-03 移入历史审计行），并把 08-04 加入必读清单；`10:24` 的“完整条件见 08-02 §6”改为“见 08-03 §4 / 08-04 §4”，统一 G0 编号（补 G0.9）。

### 3.2 B-2（高）evidence 迁移与扫描对 3 个 `.log` 不可执行

`INDEX.md:18-20` 与 `05:195-220` 都要求迁移 `p3-build3/acceptance-run1.log`、`acceptance-run2.log`、`service.err.log`，但 `.gitignore:35` 忽略 `*.log`，三者不在任何 commit 树中（`git ls-tree -r HEAD -- docs/dev/evidence/p3-build3` 只有 `acceptance-run1.sse`、`acceptance-run2.sse`、spikeQ4 两项）。

后果链：
1. `05:218-220` 的 `git restore --source=rebuild -- $item` 对这三项报 pathspec 不匹配 → Gate0 6.5 失败（foreach 不中断，失败可能被忽略）；
2. `05:115-140` 的 `git grep` 只搜跟踪文件，对三项静默返回 exit=1 → “扫描通过”是假阴性（实测 `Select-String` 能在文件内命中 `authorization`/`x-goog-api-key` 字样，虽只是 header 名列表，非真实值）；
3. 即使用 `Copy-Item` 复制，`10:29` 的 `git add -A` 也不会 stage 被忽略文件，`git status -uall` 不显示 ignored，迁移后存在性检查（`05:238-245`）又不含这三项 → 证据静默丢失。

最小修复（二选一，需用户裁决）：(a) 在 `.gitignore` 增加 `!docs/dev/evidence/**/*.log` 白名单，并在 05 的迁移段用 `Copy-Item -Force` + `git add -f`，把三项纳入存在性检查；(b) 从 INDEX/数组移除三项，改为 `git show rebuild:<path>` ref（但与“证据必须迁移”的约束冲突，需明确记录）。

### 3.3 B-3（中）SHA 记录指令互斥（R4 未闭合）

`05:96` 要求把 SHA 写入 10；`10:8` 禁止写回 10。且任何“提交后把最终 SHA 写进被提交文档”的方案都自引用（写→脏→再提交→SHA 变）。`05:96` 与 `10:8` 必须统一为同一口径。

最小修复：删除 `05:96` 的“写入 10-handoff-prompt.md”，统一改为“SHA 不写入任何被迁移文档；交接说明/审计报告以 `git rev-parse` 现查为准”；`10:8` 删除“记录到 08-03”，保留“记录到 changelog”时也不得写“最终 SHA”，只写日期/范围。

### 3.4 B-4（低）changelog 未记录 08-03/08-04

`docs/changelog.md:15-20` 的 2026-10-27 条目只覆盖 05/06/10/12，未记录 `08-03` 的提交（`4dfde35`）或本报告。08-03 §4 G0.1 要求“新 SHA 记录在 13/changelog”，当前不满足（且按 B-3 不应写 SHA）。

最小修复：新增一条 08-03/08-04 的变更记录（范围/变更/原因/验证），不写死 SHA。

### 3.5 非阻断残留

- D12-008 note “no lease/503 old contract” 与保留的“429 耗尽→503”（`05:58`、`12:61`、`csv:213` D05-026）措辞冲突，建议改为“no lease; 429→503 retained”。
- D12-006 note 说“rewrite build identity tests”，但唯一同名文件是 delete；建议补“改写落点见 D07-038（实例 auth/session）”一句，消除“rewrite vs delete”歧义。
- `08-03:5` 自述审计对象为 `1ecace7`，但该文件实际随 `4dfde35`（含 Gate0 修复）提交，基线元数据略滞后；仅影响可追溯性。

## 4. Gate0 通过条件（逐项判定）

| ID | 条件 | 现状 |
|---|---|---|
| G0.1 | 文档（含最终审计）已提交，`git status --porcelain` 为空；05/10 基线为“当前 HEAD”；SHA 记录方式无自引用 | **未过**。审计开始时工作区干净（`4dfde35`），但 08-04 为 untracked，必须先提交；`05:96` 与 `10:8` 的 SHA 指令互斥（B-3）。 |
| G0.2 | 校验脚本 `OK rows=518 ledger=518 ... errors=0` | **通过**。实测输出：`OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0`（`06-index-validation.md:20-127`）。 |
| G0.3 | D01-054..058 drop、D01-059 port、D01-060..062 keep、D03-019 drop、D04-027/028 drop、D05-026 port、D05-039 drop、D07-026 keep、D08-022 keep 三处一致；P9-Q3 不 drop 日志 | **通过**。脚本 `EXPECTED_DECISIONS`/`REQUIRED_NOTES` 全过，`errors=0`；人工复核 `06-decision-log.md:197-202,230,301,378,402` 与 CSV 一致。 |
| G0.4 | D12-004/006/008 与处置表不再矛盾 | **通过（含 §3.5 措辞残留）**。CSV/ledger 均为 port，处置表 delete/rewrite 与 note 方向一致，无 lease keep。 |
| G0.5 | secret scan 只扫迁移集合，命中仅说明文字；BOTGUARD 不复制；spikeQ4 不入库 | **部分**。跟踪文件实测只命中 `05:113/139/143`、`06-feature-index.csv:265`、`06-feature-ledger.md:320`、`ledger-batches/D06:37`（均说明文字/历史输入）；BOTGUARD 在 upstream 原生树，spikeQ4 不在数组。**但** 3 个 `.log` 无法被 `git grep` 扫描（B-2），不能宣称“全集合通过”。 |
| G0.6 | evidence 迁移与 INDEX allowlist 一一对应 | **结构通过，执行未过**。21 项数组与 INDEX 16 条“是”+3 条元文件精确对应；3 个 `.log` restore 必失败（B-2）。 |
| G0.7 | worktree/分支正确；只迁 `docs/dev/rebuild` + allowlist；无 src/tests/`-- docs`；迁移后关键文件存在 | **未过**。命令语法可执行、目标路径 `.kilo/worktrees/rebuild-upstream` 当前不存在；但 `05:195-220` 对 3 个 `.log` 失败，且 `05:238-245` 的存在性检查不含这三项，失败不可见。 |
| G0.8 | ledger-batches 仅历史输入；旧 WP 正文不入新 worktree | **通过**。`ledger-batches/README.md` 标注历史输入，`05:191-192` 删除批次 `.md` 保留 README；17 条旧 WP 清单与 archive README 一致，正文留 `rebuild`（`05:228-230`）。 |
| G0.9 | 最终审计已提交且 10 引用；10 可独立执行 | **未过**。10 引用的 08-03 已把最终裁判权让给 08-04，而 10 不引用 08-04（B-1）；08-04 尚未提交。 |

## 5. Gate0.5 通过条件

| 退出项（`12:144-150`） | 现状 |
|---|---|
| 五节接口/状态/schema/验收模板通过评审并绑定 feature ID | 文档齐备（`12:5-125`）；feature ID 绑定仍按切片填写，属预期。 |
| 处置表覆盖全部 `tests/**/*.py`，无 pending | **通过**。实测 80/80、keep 38/rewrite 37/delete 5、pending 0、feature ref 0 错。 |
| 状态表覆盖 sleep/wake/idle/rebind/429/503 | **通过**。`12:42-61` 与 `05:58` 一致，R5 的矛盾已消除。 |
| config/event schema 有示例与离线 fixture 路径 | **未通过**。路径已确定（`12:133-142`），但 `config/config.example.yaml`、`config/config.schema.json`、`tests/fixtures/events/event.sample.json`、`tests/fixtures/pg/f01..f04`、`docs/dev/rebuild/acceptance/README.md` 当前均不存在。 |
| acceptance 模板用于 P9-pre 首个切片 | 尚未发生（P9-pre 前完成）。 |

结论：Gate0.5 结构可达但**当前不通过**。这是设计内的状态；`05:258`、`10:40` 已明确“未通过不得进入 P9-pre”，不构成 handoff 阻塞（但构成 P9-pre 阻塞）。

## 6. Handoff 判定

| 项目 | 判定 |
|---|---|
| 10 是否自足 | 内容层面基本自足（路径、必读、Gate0.1-G0.8、Gate0.5、顺序、状态机红线、PG 门槛、验收格式）；但门指向错误文档（B-1）。 |
| 是否引用最终审计 | 否。引用 08-03，而 08-03 已把最终性让给 08-04（`08-03:3`）。 |
| Gate0 是否可机械执行 | 除 B-2 的 3 个 `.log` 外可执行；G0.1/G0.7/G0.9 当前未过。 |
| 校验脚本 | `errors=0`，518/518，桥接计数全对。 |
| 是否可立即下发 | **否**。先修 B-1～B-4 并提交 08-04，再由独立审计复核（`05:260-264`）。 |
| 下发后首步 | 按 10:24-32 执行 G0.1-G0.8，再按 `12 §6` 勾选 Gate0.5；未勾满不得进入 P9-pre（`05:258`）。 |

最小放行条件（4 项，均为文档改动，不改业务代码）：

1. 10 改指 08-04，纳入必读，统一 G0 编号与 G0.9；
2. 修复 `.log` 的忽略/迁移/扫描/存在性检查闭环（`.gitignore` 白名单 + `add -f`，或移除并改为 rebuild ref）；
3. 统一 SHA 口径（删 `05:96` 的“写入 10”，`10:8` 不写死最终 SHA）；
4. changelog 补 08-03/08-04 条目；提交 08-04 使工作区干净。

修复后预期：Gate0 可机械通过（`errors=0` 已实测），Gate0.5 仍待创建 config/fixture 文件后由 P9-pre 前勾选。

## 7. 实测证据（只读）

```powershell
git status --porcelain                 # 审计开始时为空
git log --oneline -1                   # 4dfde35 docs(rebuild): 重命名审计文档并修复 Gate0 输入
git rev-parse HEAD                     # 4dfde35636dc81a3965bc9f2a9f585b80db528c9
python $env:TEMP\kilo_validate_index_0804.py
# OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0

# 处置表覆盖
# files=80 listed=80（Windows 路径分隔符差异已归一）；keep=38 rewrite=37 delete=5 pending=0 bad_feature_refs=[]

# 05 扫描数组 vs 迁移数组
# scan_count=21 evidence_count=21 identical=True

# evidence 可迁移性（3 个 .log 均不在 commit 树）
git cat-file -e rebuild:docs/dev/evidence/p3-build3/acceptance-run1.log   # false
git cat-file -e rebuild:docs/dev/evidence/p3-build3/acceptance-run2.log   # false
git cat-file -e rebuild:docs/dev/evidence/p3-build3/service.err.log       # false
git ls-tree -r --name-only HEAD -- docs/dev/evidence/p3-build3
# acceptance-run1.sse / acceptance-run2.sse / spikeQ4-evidence.json / spikeQ4.py

# git grep 对 ignored 文件静默无结果（假阴性）
git grep -n -I "AIza" -- docs/dev/evidence/p3-build3/acceptance-run1.log  # exit=1
Select-String ... acceptance-run1.log -Pattern 'authorization|x-goog-api-key'  # 命中 header 名列表

git cat-file -e upstream/master:docs/BOTGUARD_VERIFICATION_CHAIN.md       # true（grandfather）
```

关键行号索引：

- 迁移/扫描：`05:96`、`:110-146`、`:161`、`:179-221`、`:232-246`；`.gitignore:35`；`INDEX.md:12-37`
- 决策/处置：`06-feature-index.csv:486-490`；`06-feature-ledger.md:571-575`；`06-test-disposition.csv:22-23,25,43-44,65,68`；`06-decision-log.md:49-50,197-202,402`
- 审计闭环：`08-03:3,11,90-98`；`10:7-8,19,22,24`；`05:260-264`；`changelog.md:15-20`
- 状态机/Gate0.5：`12:36,49-51,57,60,133-142,144-150`；`07:3`；`08-02:196-205`
