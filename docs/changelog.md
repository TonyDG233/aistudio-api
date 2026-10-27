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
