# Evidence Index

用途：Gate 0 的 evidence 迁移 allowlist 与证据索引骨架。只列已确认路径；未列出的路径默认不迁移。

## 规则

- 原始凭据、cookie、auth header、`spikeQ4` 敏感形状不得进入新仓库。
- `docs/BOTGUARD_VERIFICATION_CHAIN.md` 是 upstream 原生文件，grandfather：不复制、不改写、不计入 secret scan 失败。
- 证据只保留脱敏摘要或可离线复跑工件；需要原文时用 `git show rebuild:<path>`。
- 每条证据标注 domain、feature ID、敏感等级、迁移状态；新增证据先登记再迁移。

## 迁移 allowlist

| 路径 | Domain | 敏感等级 | 迁移 | 说明 |
|---|---|---|---|---|
| `docs/dev/rebuild/` | 全部 | 低（仅说明文字） | 是 | 文档与索引；`ledger-batches/` 仅历史输入 |
| `docs/dev/evidence/p2-verify/` | D05/D06 | 低 | 是 | SSE/log/out 验证输出 |
| `docs/dev/evidence/p3-build3/acceptance-run1.*` | D07 | 低 | 是 | build 验收输出 |
| `docs/dev/evidence/p3-build3/acceptance-run2.*` | D07 | 低 | 是 | build 验收输出 |
| `docs/dev/evidence/p3-build3/service.err.log` | D07 | 低 | 是 | 服务错误日志 |
| `docs/dev/evidence/p3-build3/spikeQ4-evidence.json` | D07 | **高** | **否** | 含 authorization/API key 形状；只留 rebuild ref |
| `docs/dev/evidence/p3-build3/spikeQ4.py` | D07 | **高** | **否** | 同上 |
| `docs/dev/evidence/p5-final-20261021/` | D05/D10/D11 | 低 | 是 | UI 截图与 README |
| `docs/dev/evidence/ui-p1-20261020/` | D11 | 低 | 是 | UI 截图 |
| `docs/dev/evidence/wp5-20261018/` | D05/D06/D07 | 低 | 是 | 旧 harness/输出/截图 |
| `docs/dev/evidence/wp5b-20261019/` | D05/D06 | 低 | 是 | 旧 harness/raw 输出 |
| `docs/dev/evidence/wp5v3-20261019/` | D05/D06 | 低 | 是 | 旧 harness/raw 输出 |
| `docs/dev/evidence/wp6-20261020/` | D10 | 低 | 是 | 统计页截图 |
| `docs/dev/evidence/wp7-20261020/` | D09/D10/D11 | 低 | 是 | UI 截图 |
| `docs/dev/evidence/wp7-v2-20261020/` | D09/D10/D11 | 低 | 是 | UI 截图 |
| `docs/dev/evidence/wp8-20261019/` | D06 | 低 | 是 | 格式层请求/流样本 |
| `docs/dev/evidence/wp10-20261019/README.md` | D12 | 低 | 是 | 证据说明 |
| `docs/dev/evidence/wp11-count-20261020/README.md` | D06 | 低 | 是 | countTokens 说明 |
| `docs/dev/evidence/wp11-responses-20261020/README.md` | D06 | 低 | 是 | Responses 说明 |
| `docs/dev/evidence/INDEX.md` | D12 | 低 | 是 | 本索引 |
| `docs/dev/evidence/p1-ark-merge-archive/README.md` | D12 | 低 | 是 | 归档规则；正文不迁移 |
| `docs/changelog.md` | D12 | 低 | 是 | 可审计变更记录 |

## 证据条目骨架

| Evidence ID | Feature ID | Domain | 路径/ref | 敏感等级 | 状态 | 工件/结论 |
|---|---|---|---|---|---|---|
| E-000 | D12-033 | D12 | `docs/dev/evidence/...` | 低 | pending | 待 P1 起逐片登记 |

## 待确认

- 每个迁移目录在 Gate 0 执行一次 scoped secret scan；命中即脱敏或排除。
- 旧 WP 正文留在 `rebuild`，本索引只链接 ref，不复制。
