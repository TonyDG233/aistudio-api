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
