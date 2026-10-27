# 08-02 Execution Readiness Re-audit

- 审计对象：`rebuild` 工作区（`c3b47e5` + 未提交文档修复），`upstream/master` `4623109`。
- 方法：只读文档、CSV/ledger、git 元数据；运行 `06-index-validation.md` 的只读校验脚本与 scoped `git grep`。未修改代码/测试/配置，未提交、未联网、未访问真实账号/IP。
- 结论：**有条件可执行，但当前状态不能直接作为 handoff 交付**。08 的 B0-1 索引错位和 B1-1 的大部分迁移风险已修复；仍存在会把旧文档复制进新 worktree、Gate0 secret scan 必然失败、决策日志与 CSV 冲突、以及 P9-pre 输入缺失等阻塞项。

## 1. 结论

| 判定 | 说明 |
|---|---|
| Gate0 流程 | 结构上可执行：worktree 路径、离线 fetch、只迁 `docs/dev/rebuild`、untracked 检查均已写入（`05-restart-map.md:109-165`）。 |
| 当前 handoff | **不可直接交付**：修复未提交，`10-handoff-prompt.md:7` 仍以 `c3b47e5` 为基线，迁移命令会复制旧文档而非本次修复。 |
| P9-pre 之后 | **不能开工**：目标 seam/状态机/config/event schema 没有交付物（`05-restart-map.md:70`、`06-decision-log.md:237`），且 sleep/wake、429/503、rebind 仍有冲突。 |
| 08/09 历史审计 | 绝大多数 B0-1 已解决；B0-2/B0-3、B1-1 的 secret/evidence 部分、B1-3 的 942 处置表仍开放。 |

## 2. 已解决

### 2.1 CSV / ledger / 桥接

- 实测校验脚本输出：`OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0`；预期输出见 `06-index-validation.md:98`。
- 518 行 CSV 与 518 个 ledger 表项逐 domain 顺序配对；`ledger_line` 全部指向实际行；feature 文本与 decision 全部一致（`06-index-validation.md:52-72`）。
- P1-P10 行数 31/43/97/44/70/25/67/42/62/37，总 518（`06-index-validation.md:27-30`）；非原生行 22/42/81/44/50/25/51/34/41/36 也与 `06-decision-log.md:31-41` 的桥接表一致。
- CSV 无 `pending`；`decision` 只有 keep/port/drop/defer（`06-index-validation.md:77-78`）。
- D05-016..019、D05-021、D05-036/037、D05-039、D06-066 已按 P2/P3 改写：CSV `06-feature-index.csv:207-212`、`:225-230`、`:300`；decision log `06-decision-log.md:140-168`。

### 2.2 Gate0 命令与迁移边界

- fetch 已明确为可选/离线步骤，并固定 remote `upstream`=DeconstructedCube、SHA `4623109`（`05-restart-map.md:109-123`）。
- worktree 从仓库根执行、先查路径、只允许 `.kilo/worktrees/rebuild-upstream`、分支已存在时不再 `-b`（`05-restart-map.md:125-138`）。
- 迁移改为 `docs/dev/rebuild` + 脱敏 evidence allowlist，禁止 `restore -- docs` 整树，禁止 checkout `src`/`tests`（`05-restart-map.md:140-155`）。
- 迁移后显式 `git add -A` + `git status -uall`，避免 untracked 漏检（`05-restart-map.md:158-165`）。
- 不再用 fork 旧版覆盖 upstream 的 `docs/ARCHITECTURE.md`（`05-restart-map.md:155`）；实测 upstream docs 只有 3 个文件，`docs/dev/rebuild` 无冲突。

### 2.3 顺序与状态机原则

- P5 已提前到 P6/P7 之前，P7 只做日志、统计页后置（`05-restart-map.md:69-78`、`06-decision-log.md:234-246`）。
- P9-pre 明确在 P1/P3/P2 前完成 lifespan/config 装配与 API Key guard（`05-restart-map.md:59-60`、`06-decision-log.md:237`、`:362-364`）。
- lease/reservation 的取消、`active_requests` 的 finally 释放、控制面不占请求容量、候选包含休眠账号，已写入 `05-restart-map.md:56-58` 与 `06-decision-log.md:50-52`。
- 429 的“先同账号重试、再唤醒/切号、保存后切换再重试、耗尽 503”已写入 `05-restart-map.md:58`、`06-decision-log.md:52`；但 CSV 仍有未改写行，见 §3.3。
- PG 门槛 F-01..F-04 + 脱敏 `[toolshape]` 已固定（`05-restart-map.md:62`、`:221`、`06-decision-log.md:55`、`09-pg-toolcall-analysis.md:280-292`）。

## 3. 仍阻塞

### B1（阻断 handoff）修复未提交，Gate0 会迁移旧文档

证据：

- 工作区状态：`M docs/dev/rebuild/05-restart-map.md`、`06-decision-log.md`、`06-feature-index.csv`、`06-feature-ledger.md`；`?? 06-index-validation.md`、`?? 10-handoff-prompt.md`。
- `05-restart-map.md:3` 与 `10-handoff-prompt.md:7` 都以 `rebuild @ c3b47e5` 为基线；而 Gate0 的迁移命令是 `git -C $new restore --source=rebuild -- docs/dev/rebuild`（`05-restart-map.md:145`）。
- `git restore --source=rebuild` 读取的是 **commit c3b47e5 的树**，不是当前工作区；`git ls-tree -r --name-only rebuild -- docs/dev/rebuild` 只有 32 个已跟踪文件，不含 `06-index-validation.md`、`10-handoff-prompt.md`。
- 因此按 10 直接 handoff，新 worktree 会得到修复前的 05/06（索引错位、旧冲突），且拿不到校验脚本和 handoff；Gate0 第一步就与本次审计结果不一致。

必须：先由用户授权提交文档修复，或把 Gate0 改为从当前工作区复制而非 `--source=rebuild`；随后把 10 的基线 SHA、05 的基线、迁移来源全部更新为新 commit，并重新冻结。

### B2（阻断 Gate0）secret scan 命令与验收标准自相矛盾

证据：

- `05-restart-map.md:104` 的扫描范围是 `-- docs`，`05-restart-map.md:209` 要求“secret scan 只命中占位符”。
- 实测该命令命中真实形状凭据：`docs/BOTGUARD_VERIFICATION_CHAIN.md:128`、`:140`、`:284`（Google API key 形状），以及 `docs/dev/evidence/p3-build3/spikeQ4-evidence.json:39`（`authorization`/SAPISIDHASH 形状）。
- `docs/BOTGUARD_VERIFICATION_CHAIN.md` 是 upstream 原生文件（`git ls-tree upstream/master -- docs` 可见），`05-restart-map.md:155` 又禁止用 fork 旧版覆盖 upstream 文档；`06-decision-log.md:371` 要求 upstream 原生无必要不改。安全扫描与“原生优先”没有裁决。
- `05-restart-map.md:148` 的 evidence 迁移只有占位符 `<sanitized-path>`，没有具体 allowlist；`docs/dev/evidence/p3-build3` 已含敏感形状，`docs/dev/evidence/p1-ark-merge-archive/`、`docs/dev/evidence/INDEX.md`、`docs/changelog.md` 均不存在（实测 `Test-Path` 为 False）。
- 结果：按 10 执行会在 Gate0 直接 FAIL，或者迁移未脱敏 evidence、或者不迁移任何 evidence（与“证据必须迁移”的项目约束冲突）。

必须：把扫描范围限定到迁移集合（`docs/dev/rebuild` + 明确 evidence allowlist），明确 upstream 原生敏感文件的 grandfather 规则；给出脱敏后 allowlist 的实际路径；决定 archive/INDEX/changelog 是在 Gate0 建立还是后置，并写入 05/10。

### B3（阻断 P9-pre/P3）决策日志与 CSV 仍有冲突，状态机未真正收敛

这些不是措辞差异，而是 D1 规定“decision log 为行为权威”（`06-decision-log.md:49`）后，agent 会做出相反实现：

1. **P9-Q3 误把日志功能标成 drop**：`06-decision-log.md:377` 写 `D01-054`, `D01-058`..`D01-062` → drop；CSV 中 `D01-059` port、`D01-060/061/062` keep（`06-feature-index.csv:60-63`）。若按 decision log 执行，会删除 setup_logging 三 sink、动态日志级别、dump 掩码和 get_logger，直接破坏 P7/D09。
2. **P3-B 与 P3-G 自相矛盾**：`06-decision-log.md:196` 把 `D03-019` 列入“其余 port”，`:201` 又把 `D03-017..019` 全部 drop；CSV 选择 drop（`06-feature-index.csv:113`）。同一 decision log 内部两条规则冲突。
3. **P3-C/P3-B 与 CSV 冲突**：`06-decision-log.md:197` 说 `D04-027` port，`:196` 说 `D04-028` port；CSV 两者都是 drop（`06-feature-index.csv:156-157`）。虽然 P9 的“删除 channel registry”（`06-decision-log.md:368`）支持 drop，但未回写 P3，agent 无法判断哪条权威。
4. **P4-Q7 与 D07-026**：`06-decision-log.md:229` 写 `D07-023..026/031` 中仅 `024` keep、其余 port；CSV `D07-026` 是 keep（`06-feature-index.csv:330`）。
5. **429 路径**：D4 要求 429 先同账号重试、耗尽 503（`05-restart-map.md:58`、`06-decision-log.md:52`）；P2-F 却把 `D05-026` 标 keep（`06-decision-log.md:163`），CSV 原文仍是“预算/候选耗尽→HTTP 429”（`06-feature-index.csv:217`），且无改写 note。`D05-038` 的 503（`:229`）与 `D05-026` 的 429 并存，验收会打架。
6. **rebind 既要求又删除**：D4 说“rebind 必须显式关闭旧实例再重建”（`05-restart-map.md:58`、`06-decision-log.md:52`）；P3 总原则又写“丢弃 pinned、后台 rebind”（`06-decision-log.md:188`），但 P2-E `D05-039` 写“pinned/rebind/defer 删除”（`:156`），CSV 同样无“background”限定（`06-feature-index.csv:230`）；P3-H 又写“手动切换保留；pinned/rebind/auto drop”（`06-decision-log.md:202`）。manual rebind 与自动 rebind 没有拆开。
7. **自动唤醒 vs idle 手动重启**：D4/P3 说消息到达唤醒所有合格“休眠/未启动”实例（`05-restart-map.md:58`、`06-decision-log.md:52`、`:184`、`:199`）；P6 说 idle browser 默认关闭、需要时手动重启（`06-decision-log.md:289`、`:300`），CSV `D08-022` 的 note 也是“idle default 0; manual restart required”（`06-feature-index.csv:370`）。若 idle 关闭浏览器，“唤醒未启动账号”就必须自动启动，和“手动重启”矛盾；若坚持手动重启，D4 的“候选包含未启动账号”无法实现。
8. **P9-pre 输入不存在且与 P5 顺序循环**：`05-restart-map.md:70` 和 `06-decision-log.md:237` 要求把“目标 seam、状态机、config/event schema”作为 P9-pre 输入；但没有对应文档（08 的 M1-M4，`08-01-execution-readiness-audit.md:78-81`）。同时 `05-restart-map.md:61` 说“统计事件 schema 在 P5 生成记账时定稿”，而 P9-pre 在 P5 之前，形成“先要 schema、后定稿 schema”的循环。

必须：修正上述 decision log 范围/行级冲突并重跑校验；新增一页最小 ADR/状态表（状态枚举、active_requests 合约、sleep/wake/rebind/429/503、capacity、控制面），明确 seam/config/event schema 的产生步骤（Gate0.5 或 P9-pre 前置），再允许进入编码。

### B4（阻断 P9-pre/P3）942 单测处置表仍未建立

- `05-restart-map.md:81` 明确要求“P10 的测试处置表在 Gate 0/P9-pre 阶段先建立，不能整体后置”；但 Gate0 §6 没有该步骤，10 也没有提及。
- 08 的 M6 要求逐文件 keep/rewrite/delete 并绑定 feature ID（`08-01-execution-readiness-audit.md:83`）；旧测试大量以 lease/多页/pinned 为契约（`08-01-execution-readiness-audit.md:62-64`），若先写新实现再处理，agent 很容易为保持 942 全绿而复活旧行为。
- 这不是“实现阶段可补”的普通缺口：它必须在 P3/P2 编码前存在，否则新测试会被旧契约反向绑架。

### B5（Gate0 冻结不完整）迁移目录仍含过期批次/待定内容

- `05-restart-map.md:145` 迁移整个 `docs/dev/rebuild`，其中 `ledger-batches/` 仍有 105 处 `待定`/`pending`（例：`docs/dev/rebuild/ledger-batches/D06-generation-protocol.md:7`、`D03-browser-context-page.md:11`）。
- `06-feature-ledger.md:3` 声称“已合并 12 个 domain 批次；pending=0”，但批次文件本身没有同步；新 agent 在迁移目录内 grep 会同时看到“已决定”和“待定”。
- `05-restart-map.md:65`、`:156` 要求旧 WP 归档到 `docs/dev/evidence/p1-ark-merge-archive/`，但目录不存在；Gate0 没有归档命令、源文件清单或目标归属（rebuild 冻结区还是新分支）。
- 处理建议：迁移时排除 `ledger-batches/`，或将其明确标为历史输入并归档；在 05/10 写明“仅主 ledger/CSV/decision log 为事实源”。

### B6（handoff 质量）10 的缺口

- 基线未更新（`10-handoff-prompt.md:7`），且未说明“文档修复必须先提交/复制工作区”。
- 仍写“G0-G11 逐项有证据”（`10-handoff-prompt.md:46`），但 G0-G11 只存在于历史审计 `08-01-execution-readiness-audit.md:129-140`；10 没给链接/定义，且 08 引用的旧 05 行号已过时。
- 没有提到 target seam/状态机/config/event schema、942 处置表、acceptance 模板、changelog/INDEX、evidence allowlist 的实际路径；这些恰是新 agent 从 Gate0 进入 P9-pre 所需。
- `05-restart-map.md:167-171` 要求“修复后独立审计再开工”，但 10 没有给出本次复审的通过条件（例如 §5 的 G0.1-G0.8）。

## 4. 实现阶段缺口（可后补，但必须绑定 gate）

| 缺口 | 何时必须 | 依据 |
|---|---|---|
| 目标 seam/ADR（Account 权威、Context/Page 工厂、surface 解析、Executor、Event Sink） | P9-pre 前 | 08 M1 `08-01-execution-readiness-audit.md:78`；05:70 |
| 实例/并发状态机（UNSTARTED/SLEEPING/STARTING/READY/BUSY/RETIRING/CLOSED + active_requests） | P9-pre/P3 前 | 08 M2 `:79`；05:56-58 |
| config schema/example（`count/surface/min_tier`、api_keys、formats、keepalive） | P9-pre 前 | 08 M3 `:80`；05:70 |
| event schema/聚合口径（request/attempt/usage/error/latency/surface/paid、client vs attempt） | P5 前（不能等 P7） | 08 M4 `:81`；05:61；06:309 |
| feature → target path/test 迁移 manifest | P1/P3/P2 逐片前 | 08 M5 `:82`；CSV 只有旧 provenance |
| 每片 acceptance 矩阵（测试项+预期+命令+工件） | P9-pre/P1 前建模板 | 08 M7 `:84`；05:222 |
| 证据治理（脱敏、secret scan、archive、INDEX、changelog） | Gate0 至少定规则，P10 收尾 | 08 M8 `:85`；05:65/204；06:397 |
| UI 决策（upstream Vue 增量 vs fork 静态产物） | P8 前 | 08 M9 `:86`；05:63；06:371 |
| Fake CDP/9998/signer、fake clock、WARP 条件 | P4/P6 前 | 08 M10 `:87`；06:288-292 |
| ModelCatalog/paid map 单一所有者接口 | P1/P7 前 | 08 M11 `:88`；06:56 |
| 文档唯一事实源规则（rebuild docs 只读/指针） | Gate0 | 08 M12 `:89`；05:99 |
| 09 的 F-08..F-13 处置 | P5/P4 前 triage | `09-pg-toolcall-analysis.md:197-262`；05:62 只列 F-01..F-07 |
| D07-044 参考项目 provenance / 离线 harness | P4 前 | `06-feature-index.csv:348`；08 B2 `:69` |

补充：

- F-05/F-06/F-07 已明确后置、不阻塞 P5（`05-restart-map.md:62`、`06-decision-log.md:55`）；F-08..F-13 没有同样的“后置/纳入”结论，尤其 F-09（前缀回退）、F-10（free/chat 403）、F-11（toolConfig 降级）会影响 P5 的工具调用契约，应在 P5 验收矩阵中列为已知限制或修复项。
- PG F-01..F-04 的验收素材已在 09 §4.2 给出（签名兜底、Struct、finish-only、工具流终块，`09-pg-toolcall-analysis.md:280-292`），但还没有 fixture 文件、测试路径和期望输出；P5 验收前需落到 acceptance 矩阵，否则 `05-restart-map.md:221` 只能算口号。
- `07-architecture-lifecycle.md:3` 仍写基线 `e3a77c2`、ledger/ledger-batches 未跟踪，与当前 `c3b47e5` 和已跟踪状态不符；05:169 要求复审 07，10 的必读列表却没有 07。至少修正 07 的基线声明，或明确 07 只读历史。

## 5. 建议 gate/命令

### Gate 0.1 文档提交与基线

```powershell
git status --porcelain
git rev-parse HEAD
# 必须为空（或只有用户批准的文件）；把新 SHA 写回 05:3 与 10:7
git show HEAD:docs/dev/rebuild/06-index-validation.md
git show HEAD:docs/dev/rebuild/10-handoff-prompt.md
```

若不允许提交，则 05:145 改为从工作区复制（例如显式 `Copy-Item docs/dev/rebuild/* $new/docs/dev/rebuild -Recurse`），并在 10 写明来源不是 `rebuild` 分支；不要同时保留 `--source=rebuild`。

### Gate 0.2 索引与决策复核

```powershell
# 运行 06-index-validation.md 的脚本（只读）
python <validation.py>

# 额外检查 05:98 未覆盖的冲突行
Select-String -Path docs/dev/rebuild/06-feature-index.csv -Pattern 'D01-059|D01-060|D01-061|D01-062|D03-019|D04-027|D04-028|D05-026|D07-026'
Select-String -Path docs/dev/rebuild/06-decision-log.md -Pattern 'P9-Q3|D03-019|D04-027|D04-028|D07-023|D05-026|D05-039'
```

通过条件：上述行在 decision log 与 CSV 中一致；否则先修 06 再进入下一步。

### Gate 0.3 Secret scan / evidence allowlist

```powershell
# 只扫迁移集合，而不是整个 docs
git grep -n -I -E "(AIza[0-9A-Za-z_-]{20,}|SAPISIDHASH|authorization:|x-goog-api-key|cookie:)" -- docs/dev/rebuild
# evidence 必须逐个 allowlist；先脱敏/复制到临时目录再验证
Test-Path docs/dev/evidence/p1-ark-merge-archive
Test-Path docs/dev/evidence/INDEX.md
Test-Path docs/changelog.md
```

通过条件：迁移集合只命中占位符/说明文字；upstream 原生 `docs/BOTGUARD...` 明确 grandfather、不计入失败；evidence allowlist 有实际路径且已脱敏。

### Gate 0.4 worktree / 迁移

```powershell
$repo = (git rev-parse --show-toplevel)
git -C $repo worktree list
Test-Path "$repo\.kilo\worktrees\rebuild-upstream"
git -C $repo worktree add .kilo/worktrees/rebuild-upstream -b rebuild/upstream upstream/master
$new = "$repo\.kilo\worktrees\rebuild-upstream"
git -C $new restore --source=rebuild -- docs/dev/rebuild
git -C $new add -A docs/dev/rebuild
git -C $new status -uall
git -C $new diff --cached --stat -- docs/dev/rebuild
```

通过条件：`06-index-validation.md` 与 `10-handoff-prompt.md` 存在于新 worktree（当前 `--source=rebuild` 不会带入）；无 `docs/BOTGUARD...`、旧 WP、`src`/`tests` 被复制；`ledger-batches/` 要么排除、要么明确标注历史输入。

### Gate 0.5 P9-pre 输入（新增）

在进入 P9-pre 前产出并评审：

1. 一页 `target-seams`/ADR：五层接口、Account 权威、Context/Page 工厂、surface 解析、Executor/active_requests、Event Sink。
2. 一页状态表：UNSTARTED/SLEEPING/STARTING/READY/BUSY/RETIRING/CLOSED；消息唤醒、idle 关闭、手动重启、rebind、429/503 的转换。
3. config schema + event schema（P5 只扩展字段，不另起账本）。
4. 942 单测逐文件 keep/rewrite/delete 表（绑定 feature ID）。
5. 每片 acceptance 模板（测试项+预期+命令+工件路径）。

没有这五项，P9-pre 的“作为输入”（`05-restart-map.md:70`）无法成立。

## 6. 验收清单

| ID | 通过条件 | 证据/命令 |
|---|---|---|
| G0.1 | 文档修复已提交或明确从工作区复制；05/10 基线 SHA 一致且为当前 HEAD | `git status --porcelain` 空；`git rev-parse HEAD`；`10-handoff-prompt.md:7` |
| G0.2 | CSV/ledger 518/518、ledger_line 全对、feature/decision 0 mismatch、桥接 518、无 pending | 校验脚本输出 `OK ... errors=0`（`06-index-validation.md:98`） |
| G0.3 | 06 与 CSV 无冲突：D01-059..062、D03-019、D04-027/028、D05-026、D05-039、D07-026 | 逐行对照 + §5 Gate 0.2 |
| G0.4 | secret scan 只在迁移集合命中占位符；evidence allowlist 已脱敏并有路径 | `git grep` scoped；allowlist 清单 |
| G0.5 | worktree 路径正确；只迁 `docs/dev/rebuild`；untracked 已 staged；无 upstream docs 覆盖 | `git worktree list`；`git status -uall`；`git diff --cached --stat` |
| G0.6 | `ledger-batches`/旧 WP/archive/INDEX/changelog 的归属已裁决 |
| G0.7 | seam/ADR、状态表、config/event schema、942 处置表、acceptance 模板已存在并通过评审 | 新文档 + 评审记录 |
| G0.8 | 10 已更新并为全新 agent 自足；G0-G11 有定义或链接；无“直接按旧 08 行号”引用 | `10-handoff-prompt.md` 全文复核 |
| P9-pre | API Key guard 默认拒绝、显式空 Key 才开放；loopback bypass 不保留；lifespan 装配可测 | 05:60/70；08 G2/G6；fake CDP/config 测试 |
| P3/P2 | 1 账号 1 Context 1 Page 1 surface；chat/build 不并存；无 lease 断言；active_requests finally；候选含休眠/未启动；429 同号重试→切换→503；保存后切换重试 | 05:56-58/210-217；08 G4/G5 |
| P5/PG | OpenAI Chat/Gemini 契约；token 口径；F-01..F-04 离线 fixture 通过；`[toolshape]` 脱敏 | 05:218-221；09:280-292 |
| P6/P7 | 24h fake clock；build-only 回写；日志 SSE/1/3/7 天；client/attempt 分母；统计不含 countTokens | 05:219-220；08 G7/G8 |

## 7. 实测摘要（只读）

- `python <06-index-validation 脚本>` → `OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0`。
- `git worktree list` → 主 worktree `c3b47e5 [rebuild]` + `.kilo/worktrees/steady-ketch`；`rebuild/upstream` 不存在，目标目录不存在。
- `git rev-parse upstream/master` → `4623109b97d14db30e304005fe1d52c29a46a7c2`；remote `upstream` 指向 DeconstructedCube。
- `git grep ... -- docs` → 命中 `docs/BOTGUARD_VERIFICATION_CHAIN.md:128,140,284` 与 `docs/dev/evidence/p3-build3/spikeQ4-evidence.json:39`；不是占位符。只扫 `docs/dev/rebuild` 时只剩说明文字/D06-031 字样。
- `Test-Path docs/dev/evidence/p1-ark-merge-archive`、`docs/dev/evidence/INDEX.md`、`docs/changelog.md` → 均为 False。
- `git ls-tree -r --name-only rebuild -- docs/dev/rebuild` → 32 个已跟踪文件；不包含 `06-index-validation.md`/`10-handoff-prompt.md`，且包含 12 个 `ledger-batches` 文件。
