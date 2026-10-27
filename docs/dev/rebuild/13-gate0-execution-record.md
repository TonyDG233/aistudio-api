# 13 Gate 0 执行记录

> 本文件包含两段：§1 为 2026-10-27 旧记录（基于 4623109，已失效）；§2 起为 99c38ba 修正记录。Gate 0 **未通过**，本文不宣告 handoff。

## 1. 历史记录（基于 4623109，已失效）

日期：2026-10-27。执行者：主线 integrator。范围：Gate 0（G0.1-G0.9）与迁移到旧 upstream 基线；不改业务代码。

| 项 | 旧值 |
|---|---|
| 源仓库/分支 | `D:\LLM\self_programming\aistudio-api` @ `rebuild` |
| 源基线（交接 SHA） | `8e0ab11` |
| 上游基线 | `upstream/master` @ `4623109b97d14db30e304005fe1d52c29a46a7c2` |
| 新 worktree | `.kilo/worktrees/rebuild-upstream`（旧记录；同一路径，现为参考） |
| 新分支 | `rebuild-upstream` |
| 迁移提交 | `cf6e681`（236 files, +8933） |
| 上游 fetch | 未执行（离线）；使用现有 ref |

旧记录中的 G0.1-G0.9 “通过”、`26/26` 测试覆盖、“Gate 0.5 自检通过”均以旧基线为前提；自 `upstream/master` 更新到 `99c38ba` 后失效，只作历史证据，不再作为开工依据。旧结论“可 handoff（有条件）”已由 `08-07` 取代。

## 2. 99c38ba 修正记录

日期：2026-10-27（UTC 18:48 起）。范围：仅 `docs/`；不改 `src/tests/config/fixtures` 业务实现，不提交、不推送、不合并。

### 2.1 基线与 fetch

| 项 | 值 |
|---|---|
| 工作分支 | `rebuild` @ `8e0ab11`（开始时干净） |
| 旧基线 | `4623109b97d14db30e304005fe1d52c29a46a7c2` |
| fetch 命令 | `git fetch upstream --prune` |
| fetch 结果 | 成功；无新 ref（`upstream/master` 已是最新），`FETCH_HEAD` 刷新于 2026-10-27T18:48:10Z |
| 实际 `upstream/master` | `99c38baefc84d8cb66b05c28ede8a49ce050bfb2` |
| 复用 worktree（参考） | `.kilo/worktrees/rebuild-upstream` @ `9b5cdea`，branch `rebuild-upstream`，`merge-base(HEAD,99c38ba)=99c38ba`，工作区干净；本次未修改。按 05 §6.4 优先复用，不再 `worktree add`；其中 12/13/acceptance 只是参考 |

### 2.2 本次文档重构

- 新增 `08-07-upstream-99c38ba-reaudit.md`：3 提交清单、76 files/+4200/-1190 diffstat、P1-P10 影响矩阵、冲突裁决；本轮补 `session.py:211` blue-green 行（rewrite：先关旧 Page/Context 再建新页，禁止 `_retiring_contexts` 后台双页）。
- 修订 `05/06/10/12`：新基线、fetch 硬门、Gate0/0.5、状态机、config/event schema、测试基线；worktree 统一为复用 `rebuild-upstream`，禁止 `rebuild/upstream`。
- 新增 `13`（本文件）；`08-06` 顶部加历史横幅。
- `06-test-disposition.csv` 由 80 行扩到 82 行：补 `test_account_presets.py`、`test_tier_preemption.py`。
- `06-feature-index.csv` / `06-feature-ledger.md`：仅调整 `D01-012`、`D01-038`、`D03-004`、`D03-009`、`D03-035`、`D04-042`、`D08-022` 的 decision/note；`D03-004`、`D03-009`、`D08-022` 为 rewrite；518 行集合不变。
- 所有改动**未提交**，等待独立 reviewer；因此 Gate 0.1 尚不能通过。

### 2.3 校验输出（实际）

索引脚本（`06-index-validation.md` 原脚本）：

```text
OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0
```

测试处置：

```text
06-test-disposition.csv rows=82, pending=0
main rebuild tests/**/*.py = 80, covered = 80/80
upstream 99c38ba tests/**/*.py = 28 (incl conftest), covered = 28/28
```

说明：旧记录写 `26/26`；26 是 `296680f/b8af406` 的测试文件数，`99c38ba` 新增 2 个测试文件后为 28。`26/26` 与主 `rebuild` 的 `80/80` 不是同一口径，不能互相冒充。

残留检索（`4623109` / `fetch 可选` / `08-06 可 handoff` / `browser_idle_timeout`）：

```text
05: 仅历史说明（旧基线作废、fetch 硬门、Gate0.5 删除 idle 键）
06-decision-log: 仅 99c38ba 裁决中的历史替代说明
06-feature-ledger / 06-feature-index: 4623109 为 provenance；idle/watchdog 行已按 99c38ba 调整
08-01..08-06: 历史审计原文，保留
08-07 / 13: 历史基线记录与移除说明
10: 仅“08-06 为历史、不得作为 handoff 依据”与“删除 idle 键”说明
```

### 2.4 Gate 状态（不宣称通过）

| ID | 状态 | 说明 |
|---|---|---|
| G0.1 基线 | **未通过** | 文档改动未提交，工作区非干净；需提交后记录新 HEAD。 |
| G0.2 上游硬门 | **通过（本次）** | fetch 成功，`upstream/master=99c38ba` 已记录。 |
| G0.3 索引 | **通过（本次）** | `errors=0`，518/518，桥接计数正确。 |
| G0.4 决策 | **通过（文档）** | 08-07 已裁决 99c38ba 冲突；无 pending。 |
| G0.5 测试处置 | **通过（文档）** | 82 行；主 80/80；99c38ba 28/28；pending 0。 |
| G0.6 Secret/evidence | **通过（本次，allowlist 完整）** | 按 05 §6.2 实际扫描 `docs/dev/rebuild` 全目录 + evidence allowlist 17 项 + 11 个 ignored `.log` + `docs/changelog.md`，missing 0；命中仅为扫描命令、header 名列表、feature 名称或历史审计说明，无真实 key/cookie/header 值。evidence 内容未改。 |
| G0.7 worktree/迁移 | **规则已冻结，未执行迁移** | 05 §6.4 优先复用现有干净 worktree（branch `rebuild-upstream`，`merge-base=99c38ba`），不再 `worktree add`；复用后必须删除旧 `acceptance/config/tests fixtures` 并由 Gate 0.5 按 12 §5.1 重建/验证。本次只改主工作区 docs，迁移由后续 agent 执行。 |
| G0.8 归属 | **沿用历史** | ledger-batches/旧 WP 规则不变。 |
| G0.9 Gate0.5 | **未通过** | 按新流程先删除旧 worktree 的 config/fixture/acceptance，再由 Gate 0.5 按 12 §5.1 重建/验证；当前尚未执行。 |
| G0.10 Handoff | **未通过** | 需独立 reviewer 判定；本文件不宣告 handoff。 |

### 2.5 明确不能声称的事

- 不声称 Gate 0 已通过；G0.1 需文档提交后才成立。
- 不声称 Gate 0.5 已通过；§4 工件需在迁移 worktree 按 99c38ba + 本文件 §3 schema 重新创建/验证。
- 不声称 `08-06` 的“可 handoff（有条件）”仍有效；该结论基于 4623109。
- 不声称 26/26 等于 80/80。
