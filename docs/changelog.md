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

## 2026-10-28

- 范围：`docs/dev/rebuild/05`、`06-P4-questions`、`06-decision-log`、`06-feature-ledger`、`08-09`、`08-10`、`10`、`12`、`13`；`docs/changelog.md`
- 变更：收敛 Build 事故记录范围。P4 当前未开始；`08-10` 只记录事故并要求到 P4 Build 实现阶段启动时再做前期探索/可行性重试；`08-11` 降为辅助附录，不进入必读/Gate/验收/Q4；删除当前 B0a/B0b 执行门表述。
- 原因：文档会让人误以为已经进入 P4、正在修复 Build；实际当前仍在 Gate0/计划阶段。
- 验证：仅 docs 变更；P4 未实现；未访问真实账号/IP/Google。
- 状态：事故记录保留；不宣告 P4 开始、Build 可用或 handoff。

- 范围：`docs/dev/rebuild/05`、`06-P4-questions`、`06-decision-log`、`06-feature-ledger`、`08-09`、`08-10`、`10`、`12`、`13`；`docs/changelog.md`
- 变更：拆分 `P4-B0a`（实现前离线溯源，产出 `08-11-build-400-trace.md` 并写回 Q4，需独立 reviewer 签字）与 `P4-B0b`（实现后 fail-fast/错误透传/native-signer/9998 无循环重试验收）；阻塞范围限定为 native/transport/Q4 相关实现，signer-only 离线 spike 与非传输 P4 可继续。
- 原因：独立 reviewer 指出单门“实现前/验收前”摇摆、08-11 交付物缺失、12/10 门类别混用、Q4 旧结论残留。
- 验证：HEAD `1bc0efc`，工作区干净；仅 docs 变更；`08-11` 尚未产出，B0a 未通过。
- 状态：历史条目；已被上一条范围收敛取代，当前不执行 B0a/B0b，不作为 P4 门。

- 范围：`docs/dev/rebuild/06-P4-questions.md`、`06-decision-log.md`、`06-feature-ledger.md`、`05`、`08-10-build-surface-incident-20261028.md`、`10`、`12`、`13`；`docs/changelog.md`
- 变更：记录 Build native `streamGenerateContent` 返回 `400 INVALID_ARGUMENT` / `PROXY_GENERATION_DISABLED` 的事故；新增 P4-B0 门（后被上一条拆分为 B0a/B0b）；禁止把 9998 重连当作修复。
- 原因：2026-10-28 12:04:21/22 的 build_surface 日志；P4-Q4 旧“native 主 + signer 回退”不再可直接作为实现依据。
- 验证：事故日志摘录、08-10 §4/§5 清单；P4 未实现、未访问真实账号/IP/Google。
- 状态：历史条目；P4-B0 单门描述已被上一条 B0a/B0b 条目取代。

- 范围：`docs/dev/rebuild/05`、`06*`、`08-07`、`08-09`、`10`、`12`、`13`；`docs/changelog.md`
- 变更：新增 `08-09-decision-record.md`，把用户确认的 D-01..D-14、17 个 `NO_FEATURE_ROW` 分类（16 feature + 1 out-of-scope）、20 个冲突闭合、`ENGINEERING_DEFAULT` 清单与 F1-F8 阶段固定为行为权威；修订 05/06/10/12/13 与 08-07 历史横幅。
- 原因：08-08 全量审计发现 14 项待决策、17 个 `NO_FEATURE_ROW`、20 个冲突；08-07 领域级审计已被证明有 16 处漏项/错项，不能作为 handoff 依据。
- 验证：`06-index-validation.md` 脚本输出 `OK rows=534 ledger=534 packages=34/47/98/44/74/25/69/42/64/37 total=534 errors=0`；`06-test-disposition.csv` 86 行；`git diff --stat` 仅 `docs/`。
- 状态：未提交，不推送；Gate 0 未通过、Gate 0.5 未执行；是否可 handoff 由后续独立 reviewer 判定。

- 范围：`docs/dev/rebuild/05`、`06*`、`08-09`、`10`、`12`、`13`；`docs/changelog.md`
- 变更：按独立 reviewer BLOCKED 修复 9 项：D-14 落到 `D11-015`/`test_api_auth`；`D03-018` 改 rewrite（一次有界 drain，无后台常驻 task/第二页）；`active_requests` 区分“手动关闭可拒绝”与“禁用/预设 rebind 不得直接拒绝”；E-01 收敛为 8 个端点；drain 只在旧 Page/Context 且期间不建新页；ledger 分布改为实际 534；`D06-028` 改 rewrite（hash 代码 part + 诊断开关）；test-disposition 补 quota_scope/403 体/finish/seed/锁用例；12/10 补 SettingsView/ApiKeyManagerCard UI 验收。
- 原因：独立 reviewer BLOCKED 的 9 项，以及 toggle/batch 措辞、F0/F3 边界、P8 D-14 注、测试理由等非阻断清理。
- 验证：索引脚本输出 `OK rows=534 ledger=534 packages=34/47/98/44/74/25/69/42/64/37 total=534 errors=0`；`06-test-disposition.csv` 86 行；`git status --short` 仅 docs。
- 状态：未提交；Gate 0 未通过、Gate 0.5 未执行；不宣告 handoff。

## 2026-10-27

- 范围：`docs/dev/rebuild/08-08-upstream-99c38ba-full-diff-audit.md`；`docs/changelog.md`
- 变更：新增上游 `4623109..99c38ba` 的 76/76 逐文件全量审计报告。报告发现 17 项 `NO_FEATURE_ROW`、20 项冲突、14 项待用户决策，并列出 08-07/10/12/13 的 16 处漏项/错项；本提交只落审计证据，不修改既有裁决、不实施修复。
- 原因：08-07 只有领域级审计，无法支撑逐功能台账和后续冲突处理；用户要求独立全量审计。
- 验证：报告 §3 覆盖清单 76 行，与 `git diff --numstat 4623109..99c38ba` 对齐；报告 §11 明确未运行上游测试、未访问真实账号/IP/Google。
- 状态：审计报告已提交；handoff 仍被阻塞，修复未开始，待 §6 的 D-01..D-14 决策。

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
