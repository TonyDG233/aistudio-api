# 08-03 Final Readiness Audit

> 历史快照：本报告记录修复前发现的问题；其后文档已按 R1-R7 修复，最终可用性以 `08-04-final-readiness-audit.md` 为准。

- 审计对象：`rebuild` @ `1ecace7`（工作区干净），`upstream/master` @ `4623109`。
- 方法：只读文档/CSV/ledger/git 元数据；运行 `06-index-validation.md` 校验脚本；scoped `git grep` 复核 secret scan 范围；逐文件核对测试处置表与实际 `tests/**/*.py`。未修改代码/测试/配置，未提交/推送/合并，未联网，未访问真实账号/IP。
- 性质：这是对第二轮修复（`08-02-execution-readiness-reaudit.md` B1-B6）之后状态的**最终独立复审**，只新增本报告，不回改 05/06/08/09/11/12。

## 1. 结论

**有条件可 handoff（Gate0 结构已可达，但有 4 项文档矛盾必须在下发前消除并提交）。**

| 维度 | 判定 |
|---|---|
| B1 提交与迁移来源 | 已解决（`1ecace7` 已提交，05/10 不再写死旧 SHA，`restore --source=rebuild` 有明确前提）。 |
| B2 secret scan / evidence | 基本解决（范围已收窄、grandfather/spikeQ4 有裁决、INDEX/archive/changelog 均已入库）；但**证据迁移命令与 allowlist 不闭合**。 |
| B3 决策冲突 | 11 点名的 7 组冲突**全部消除**；但发现 1 组它没覆盖的同类冲突（D12-006 租约测试 keep vs dispose delete）。 |
| B4 942 处置表 | 已解决（80/80 覆盖、0 pending、feature ID 全部可解析）。 |
| B5 ledger-batches / 旧 WP | 已解决（README 标注历史输入 + Gate0 删除；17 条 WP 两处清单一致）。 |
| B6 handoff 自足性 | 基本解决（G0.1-G0.8 内联、引用 12/Gate0.5、无旧行号硬依赖）；缺“本复审通过后才可下发”的闭环，且 07 基线已过期。 |
| P9-pre 可开工 | **尚不可**：Gate0.5 退出项（schema 示例/fixture 路径）未落实，且 12 §2 状态表与并发红线自相矛盾。 |

一句话：第二轮把“Gate0 会复制旧文档 / 必然 FAIL / 事实源互相打架”这三类结构性问题修掉了；剩下的都是**局部、低成本但会直接误导实现**的文档矛盾，修完并提交即可 handoff。

## 2. B1-B6 状态表

| ID | 11 的原始阻塞 | 现状 | 证据 |
|---|---|---|---|
| B1 | 修复未提交，`restore --source=rebuild` 会迁移旧文档 | **已解决**。`1ecace7` 已包含 05/06/10/12/处置表/INDEX/changelog；05/10 只写“当前 HEAD”，无旧 SHA；迁移命令有“已提交才可用”前提，且迁移后有存在性检查 | `05-restart-map.md:96-98`、`:160-164`、`:184-197`；`10-handoff-prompt.md:7-8`、`:26`；`git log -1` = `1ecace7`；`git ls-tree -r HEAD -- docs/dev/rebuild` 38 个文件含 06-index-validation/10/12/06-test-disposition |
| B2 | 扫描 `-- docs` 必然命中真实凭据；allowlist/archive/INDEX/changelog 不存在 | **基本解决**。扫描范围收窄到 `docs/dev/rebuild` + allowlist；BOTGUARD grandfather、spikeQ4 排除、索引/归档/变更日志均已建立。残留：迁移命令只 restore 3 个证据文件，与 INDEX 的 13 项“迁移=是”不一致；扫描示例只列 2 个目录 | `05-restart-map.md:108-121`、`:169-172`；`docs/dev/evidence/INDEX.md:12-37`；`p1-ark-merge-archive/README.md:13-37`；`docs/changelog.md:17-23`；实测 scoped scan 仅命中说明文字 |
| B3 | 7 组 decision log / CSV / 行为口径冲突 | **已解决（点名项）**。P9-Q3 现为 `D01-054..058 drop`、`D01-059..062 保持 port/keep`；D03-019 只归 drop；D04-027/028 双 drop；D05-026 = 同号重试→切换→503；D05-039 明确“后台 drop / 手动保留”；D07-026 keep；D08-022 与 12 状态表一致 | `06-decision-log.md:197-198`、`:202`、`:230`、`:156`、`:164`、`:301`、`:378`；`06-feature-index.csv:55-63`、`:113`、`:156-157`、`:217`、`:229-230`、`:330`、`:370`；校验脚本 `errors=0` |
| B4 | 942 处置表未建立 | **已解决**。80 个测试文件全部登记，keep 38 / rewrite 37 / delete 5，0 pending，全部 feature ID 可在 CSV 解析 | `06-test-disposition.csv:1-81`；实测 `files=80 listed=80 missing=0 extra=0 bad_feature_refs=0` |
| B5 | ledger-batches 仍含“待定”；旧 WP 无归档归属 | **已解决**。`ledger-batches/README.md` 明确历史输入且 Gate0 排除批次文件；旧 WP 17 条清单在 05 与 archive README 完全一致，正文留 rebuild | `ledger-batches/README.md:3-7`；`05-restart-map.md:65`、`:166-167`、`:180-182`；`p1-ark-merge-archive/README.md:13-33`；`06-feature-ledger.md:3` |
| B6 | 10 不自足、依赖旧 08 行号、无复审通过条件 | **基本解决**。10 内联 G0.1-G0.8、指向 12/Gate0.5、给出必读清单与验收口径；G1-G11 只作章节级引用（08:129-140）不再依赖行号。残留：未把 13 纳入必读、未写“复审通过后才可下发”；07 基线过期 | `10-handoff-prompt.md:11-19`、`:21-29`、`:31-37`、`:60`；`05-restart-map.md:212-216`；`07-architecture-lifecycle.md:3` |

## 3. 仍阻塞

### 3.1 Gate0 必须（下发前修，均为文档改动）

**R1（中）证据迁移集合不闭合。**
`05-restart-map.md:169-172` 只 restore `INDEX.md`、`p1-ark-merge-archive/README.md`、`docs/changelog.md`；而 `docs/dev/evidence/INDEX.md:17-34` 把 `p2-verify/`、`p3-build3/acceptance-run*`、`p3-build3/service.err.log`、`p5-final-20261021/`、`ui-p1-*`、`wp5*`、`wp6*`、`wp7*`、`wp8*`、`wp10/11 README` 标为“迁移=是”。按 05 执行，新 worktree 只有索引没有证据正文，与 `05:65` 的 D6 边界和“证据必须迁移”约束冲突；新 agent 要么漏迁，要么自行发明未审计的复制命令。
同时 `05:115` 的扫描示例只列 `p2-verify`、`p5-final-20261021` 两个目录，与 allowlist 的 13 项不对应（本次已实测其余目录干净，见 §6，但命令不完整）。
建议：把 allowlist 写成显式路径数组 + `foreach restore`，并对同一数组跑扫描。

**R2（中）D12 租约测试在 CSV/decision log 仍是 keep，处置表却是 delete。**
`06-feature-index.csv:488` 与 `06-feature-ledger.md:573` 把 `D12-006 Build 身份=租约（2 例）` 记为 `keep`，`06-decision-log.md:402`（P10-Q1）也把 `D12-006` 列入 keep/port；但 `06-test-disposition.csv:23` 将 `tests/unit/test_build_lease_identity.py` 判为 `delete`（理由“lease/pinned identity 契约整体删除”）。同类：`D12-008` note 仍写“释放租约”（`06-feature-index.csv:490`），`D12-004` keep 却引用被 delete 的 `test_session_blue_green_readiness.py`（`:486` vs `06-test-disposition.csv:68`）。
影响：P10 的“单测基线”与 D2“取消 lease”直接对立，新 agent 若按 CSV keep 会复活租约测试，进而反向绑架 P3/P2 实现（正是 11 B4 警告的失败模式）。
建议：D12-006 改 drop 或 note 写明“改写为实例 auth/session”；D12-004/008 note 删除“租约/blue-green 测试已在处置表 delete/rewrite”的歧义。

**R3（中）本报告与“审计闭环”尚未进入迁移集合。**
`13` 目前是 untracked；`05:96` 要求 handoff 前 `git status --porcelain` 为空，`05:161` 的 `restore --source=rebuild` 只搬 commit 树。因此：若提交 13，其 SHA 会变、需重记；若不提交，则 Gate0 被 05:96 中止，或强行 restore 后新 worktree 没有“复审通过”这一结论，`10` 也没有任何指向 13 的句子。
建议：把 08-03 提交，并在 `10-handoff-prompt.md:21` 前加一行“本提示词仅在 `08-03-final-readiness-audit.md` 结论为‘可 handoff’后使用；修复后须再由独立审计复核（05 §6.7）”。

**R4（低）05:96-98 的“记录 SHA”存在自引用循环。**
先 commit → 把 SHA 写进 10 → 工作区又变脏（违反 `:96` 的 clean 检查）→ `restore --source=rebuild` 取回的 10 又不含刚写的 SHA。当前 10:7 已改为“当前 HEAD”，循环不致命，但“填入本行”这条指令按字面无法同时满足。
建议：SHA 记录在 13/changelog（随文档一起提交），10 保持“当前 HEAD + 以 `git rev-parse` 现查为准”。

### 3.2 Gate0.5 必须（进入 P9-pre 前修）

**R5（高，P9-pre 直接输入）12 §2 状态表与并发红线矛盾。**
`12-gate0-inputs.md:36` 写“关闭/改绑前检查 `active_requests`；大于 0 拒绝”，`:57` 写“关闭/改绑被拒直到归零”；但 `:50` 的 BUSY 行允许 `BUSY → RETIRING（拒绝新请求）`，即 `active_requests>0` 时进入关闭态。实现者可以据此做出“优雅 drain”而非“拒绝关闭”，直接违反 D2/P3 与 `05:56`、`06-decision-log.md:50`。
建议：把 BUSY 的允许离开改成仅 `READY`（全部释放）；RETIRING 只从 READY/无在途进入，或明确“RETIRING 仅由显式 close 在归零后触发”。

**R6（中）Gate0.5 退出项 4 未落实。**
`12:138` 要求“config/event schema 有示例与离线 fixture 路径”，但 §3/§4（`12:63-111`）只有内联代码块，没有示例文件路径或 fixture 路径；`12:129-131` 的 F-01..F-04 也没有 fixture 路径（11 §4 已指出）。Gate0.5 未通过则 `05:210` 禁止进入 P9-pre。
建议：在 12 内补 `config.example.yaml`、`config.schema`、`event.sample.json`、`tests/fixtures/pg/*` 的目标路径（可以是“待创建”的确定性路径），否则 checklist 永远勾不上。

**R7（低）07 基线过期。**
`07-architecture-lifecycle.md:3` 仍写 `rebuild @ e3a77c2` 且“ledger/ledger-batches 未跟踪”，与当前 `1ecace7`、已跟踪状态不符。10 的必读清单未列 07，风险限于“agent 自行翻阅后误判事实源状态”。
建议：把 07 首行改为“历史快照（当时基线 e3a77c2），仅只读参考”，或更新基线。

### 3.3 实现阶段可补（不阻塞 handoff，须绑定 gate）

| 缺口 | 必须时点 | 依据 |
|---|---|---|
| feature → target path/test manifest（M5） | P1/P3/P2 逐片前 | `08-01-execution-readiness-audit.md:82`；CSV 仍只有旧 provenance |
| UI 决策 ADR（M9，Vue 增量 vs fork 静态产物） | P8 前 | `08:86`；`05:63`；`06-decision-log.md:371` |
| Fake CDP/9998/signer、fake clock、WARP 条件（M10） | P4/P6 前 | `08:87`；`06-decision-log.md:288-292` |
| ModelCatalog/paid map 单一所有者接口（M11） | P1/P7 前 | `08:88`；`06-decision-log.md:56` |
| 09 的 F-08..F-13 triage | P5/P4 前 | `09-pg-toolcall-analysis.md:197-262`；`10:56` 已承接 |
| D07-044 参考项目 provenance / 离线 harness | P4 前 | `06-feature-index.csv:348`；`08:69` |
| 每片 acceptance 实际工件目录 | P9-pre 起逐片 | `12:113-131` 只有模板，无 `acceptance/` 实际目录 |

补充：F-05/06/07 已明确后置（`05:62`、`10:56`）；F-08..F-13 已被 10 承接为“P5/P4 前 triage”，不再是无声缺口。P9-pre 本身（lifespan 装配 + API Key 默认拒绝）在 05/06/12 口径一致，无冲突。

## 4. Gate0 通过条件（建议冻结为可机械执行的清单）

| ID | 通过条件 | 判定方式 |
|---|---|---|
| G0.1 | 文档（含 13）已提交，`git status --porcelain` 为空；05:3 与 10:7 的基线为“当前 HEAD”；新 SHA 记录在 13/changelog | `git status --porcelain`、`git rev-parse HEAD` |
| G0.2 | 校验脚本 `OK rows=518 ledger=518 ... total=518 errors=0` | 运行 `06-index-validation.md:20-127` |
| G0.3 | D01-054..058 drop / D01-059 port / D01-060..062 keep / D03-019 drop / D04-027-028 drop / D05-026 port / D05-039 drop / D07-026 keep / D08-022 keep 在 CSV、ledger、decision log 三处一致 | 已通过（`errors=0` 且人工复核 `:197-198,:202,:230,:378`） |
| G0.4 | 含 R2 修复：D12-004/006/008 与 `06-test-disposition.csv` 不再互相矛盾 | CSV:486-490 vs disposition:23/68 |
| G0.5 | secret scan 只扫迁移集合，命中仅为说明文字；BOTGUARD grandfather 不复制；spikeQ4* 不进入新仓库 | `05:113-121`；本次实测通过 |
| G0.6 | evidence 迁移与 INDEX allowlist 一一对应（含 R1） | `INDEX.md:17-34` 对 `05:169-172` |
| G0.7 | worktree 路径/分支正确；只迁 `docs/dev/rebuild` + allowlist；无 src/tests/`-- docs` 整树；迁移后 6 个关键文件存在 | `05:140-152`、`:184-197` |
| G0.8 | ledger-batches 仅历史输入（README + 删除批次）；旧 WP 正文不入新 worktree | `ledger-batches/README.md:3-7`；`05:166-167`、`:180-182` |
| G0.9 | 08-03 已提交且 10 引用 13；10 可被全新 agent 独立执行 | `10:11-19`、`:21-29` |

## 5. Handoff 可用性

| 项目 | 判定 |
|---|---|
| 10 是否自足 | 基本自足：路径、基线策略、必读顺序、Gate0.1-0.8、Gate0.5 五项、顺序、状态机红线、PG 门槛、验收与报告格式齐备（`10:5-62`）。 |
| 是否引用 12 / Gate0.5 / G0.1-G0.8 | 是（`10:16`、`:21-29`、`:31-37`）。 |
| 是否仍依赖旧 08 行号 | 否；只在验收处引用 08 的“验收清单”章节 + 12 §5，并显式提示“不要只按旧 08 行号”（`10:60`）。 |
| 是否明确“修复后重新审计再开工” | 05 明确（`05:212-216`），**10 未写**；需按 R3 补一行并引用 13。 |
| 是否可立即下发 | **否**。先修 R1/R2/R3（Gate0），再由本报告转为“可 handoff”；R5/R6 在 Gate0.5 前修，否则 P9-pre 仍不可开工。 |
| 下发后首步 | 按 10:22-29 执行 G0.1-G0.8，再按 12 §6 勾选 Gate0.5 退出项；任何一项未过不得进入 P9-pre（`05:210`）。 |

## 6. 证据 / 命令（本次实测）

```powershell
git status --short                 # 空
git log --oneline -1               # 1ecace7 docs(rebuild): 修复 Gate0 决策冲突与迁移输入
git ls-tree -r --name-only HEAD -- docs/dev/rebuild
# 38 个文件，含 06-index-validation.md / 10-handoff-prompt.md / 12-gate0-inputs.md / 06-test-disposition.csv / ledger-batches/README.md

# 校验脚本（从 06-index-validation.md 提取后运行，只读）
python $env:TEMP\validate_index.py
# OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0

# 测试处置表覆盖
# tests/**/*.py = 80；CSV 数据行 = 80；missing=0 extra=0；pending=0；keep=38 rewrite=37 delete=5
# disposition 中所有 Dxx-NNN 均可在 feature-index 解析（bad_feature_refs=0）

# secret scan（迁移文档集合，仅命中说明文字）
git grep -n -I -E "(AIza[0-9A-Za-z_-]{20,}|SAPISIDHASH|authorization:|x-goog-api-key|cookie:|Bearer [A-Za-z0-9._-]{20,})" -- docs/dev/rebuild
# 命中：05:113/115/118（命令与通过条件原文）、06-feature-index.csv:265 与 ledger:320（D06-031 功能名）、
#       ledger-batches/D06:37（历史输入）、08:50 与 11:60（对敏感形状的描述）——均非真实值

# secret scan（INDEX allowlist 内除 p3-build3 之外的目录）
git grep -n -I -E "(AIza[0-9A-Za-z_-]{20,}|SAPISIDHASH|authorization:|x-goog-api-key|cookie:|Bearer [A-Za-z0-9._-]{20,})" -- docs/dev/evidence/p2-verify docs/dev/evidence/p5-final-20261021 docs/dev/evidence/ui-p1-20261020 docs/dev/evidence/wp5-20261018 docs/dev/evidence/wp5b-20261019 docs/dev/evidence/wp5v3-20261019 docs/dev/evidence/wp6-20261020 docs/dev/evidence/wp7-20261020 docs/dev/evidence/wp7-v2-20261020 docs/dev/evidence/wp8-20261019
# exit=1（无命中）

# p3-build3：真实形状只存在于被排除的 spikeQ4 文件
git grep -n -I -E "(AIza[0-9A-Za-z_-]{20,}|SAPISIDHASH|authorization:|x-goog-api-key)" -- docs/dev/evidence/p3-build3
# spikeQ4-evidence.json:14,39,41；spikeQ4.py:66 —— INDEX:21-22 / 05:120 已标“不迁移”

# 存在性
# docs/dev/evidence/INDEX.md / p1-ark-merge-archive/README.md / docs/changelog.md / 12-gate0-inputs.md / 06-test-disposition.csv 均为 True 且已跟踪
# docs/dev 根 17 个旧 WP 文件 = 05:182 清单 = archive README 17 行
```

关键文件行号索引（供复核）：

- 提交与迁移：`05-restart-map.md:96-98`、`:154-173`、`:184-198`；`10-handoff-prompt.md:7-8`、`:26`
- 扫描与证据：`05-restart-map.md:108-121`；`docs/dev/evidence/INDEX.md:12-37`；`p1-ark-merge-archive/README.md:5-11`、`:35-38`
- 状态机/输入：`12-gate0-inputs.md:33-40`、`:42-61`、`:63-84`、`:86-111`、`:133-139`；`05-restart-map.md:52-63`
- 决策一致性：`06-decision-log.md:49-52`、`:150-168`、`:177-203`、`:290-301`、`:378`、`:402-409`
- 冲突残留：`06-feature-index.csv:486-490` vs `06-test-disposition.csv:23`、`:68`；`12-gate0-inputs.md:36` vs `:50`；`07-architecture-lifecycle.md:3`

## 7. 复审结论

- Gate0：**结构可达**，修完 R1-R4 并提交后可按 `05 §6.1-6.5` 机械执行，预期 `errors=0`、scoped scan 只命中说明文字。
- Gate0.5：**尚不可勾满**；R5/R6 属 Gate0.5 必过项，未修则 `05:210` 禁止进入 P9-pre。
- Handoff：**有条件可用**；10 的内容质量已够新 agent 独立执行，缺的是 R3 的审计闭环一句话与 R5/R6 的输入补全。
