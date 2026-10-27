# Changelog

可审计的文档/架构/行为变更记录；git log 之外的补充。

格式：

```text
## YYYY-MM-DD
- 范围：文件/domain
- 变更：一句话
- 原因：决策 ID / 审计项
- 验证：命令或工件路径
```

## 2026-10-27

- 范围：`docs/dev/rebuild/05`、`06*`、`08-06`、`08-07`、`10`、`12`、`13`；`docs/changelog.md`
- 变更：上游基线由 `4623109` 重对齐到 `99c38ba`；新增 08-07 影响矩阵与 P1-P10 裁决；fetch 改为 Gate 0 硬门；`08-06` 标为历史、结论失效；补 99c38ba 的账号预设/软配额/tier 抢占/idle 移除/wire/transport/config/stats 裁决；测试处置补 2 个上游测试（82 行）。
- 原因：`upstream/master @ 99c38ba` 变更；旧 08-06 基于 4623109 的 handoff 结论不再有效。
- 验证：`06-index-validation.md` 脚本输出 `OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0`；`06-test-disposition.csv` 82 行、pending=0、主 80/80、99c38ba 28/28；`git diff --stat` 仅 docs/。
- 状态：文档未提交，等待独立 reviewer；不宣告 handoff。

- 范围：`docs/dev/rebuild/05`、`06*`、`08-07`、`10`、`12`、`13`；`docs/changelog.md`
- 变更：按独立 reviewer BLOCKED 修复 5 项：worktree 复用规则统一为 `.kilo/worktrees/rebuild-upstream`（branch `rebuild-upstream`，merge-base 99c38ba，不再 `rebuild/upstream`）；08-07 新增 blue-green 裁决并同步 rebind 状态机；`test_browser_engine/test_cdp_client/test_session_lifecycle` 改 rewrite；`D03-004/D03-009/D08-022` 改 rewrite；13 的 G0.6 补扫完整 allowlist。
- 原因：独立 reviewer 对未提交文档判 BLOCKED；旧 worktree 的 12/13 只作参考，事实源为主工作区 docs。
- 验证：索引脚本 `errors=0`；disposition 82 行、pending=0、主 80/80、99c38ba 28/28；G0.6 扫描 30 个目标、missing=0，命中仅为扫描命令/header 名/历史说明。
- 状态：仍不提交、不宣告 handoff。

- 范围：`docs/dev/rebuild/05`、`06*`、`10`、`12`
- 变更：按第二轮复审修复 Gate 0/0.5 阻塞；明确 429/rebind/sleep-wake/idle 口径；新增测试处置表与 P9-pre 输入。
- 原因：`08-02-execution-readiness-reaudit.md` B1-B6、G0.1-G0.8。
- 验证：`06-index-validation.md` 脚本输出 `errors=0`。

- 范围：`docs/dev/rebuild/08-01/08-02/08-03/08-04/08-05/08-06`
- 变更：审查文档统一为 `08-0x` 命名；闭合 D12 租约测试、状态机、证据 allowlist、handoff 验收门。
- 原因：`08-04-final-readiness-audit.md` R1/R3/R4、`08-05-final-readiness-audit.md` 剩余项。
- 验证：`08-06-final-readiness-audit.md` 复核通过（有条件）；基线 `0f9357b`，交接 HEAD 现查。`06-index-validation.md` 输出 `errors=0`。

- 范围：`docs/dev/rebuild/05`、`06-feature-index.csv`、`08-05`、`08-06`、`10`；`docs/changelog.md`
- 变更：修正 05 的文档编号引用、10 的 G1-G11 固定出处、D12-006/008 的 decision note；08-05 顶部加“已被 08-06 取代”历史横幅；记录 handoff 基线 SHA。
- 原因：`08-06-final-readiness-audit.md` §3.2 非阻断残留；G0.1 交接基线。
- 验证：`06-index-validation.md` 脚本输出 `OK ... errors=0`；交接基线 `7cd92f7dcb2b8675050aa342a38b9853a27e33c4`。
