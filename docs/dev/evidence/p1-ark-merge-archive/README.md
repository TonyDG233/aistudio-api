# P1 Ark Merge Archive（归档规则）

用途：存放旧 WP/规划文档的归档规则与清单。**本目录当前只放说明，不搬运真实凭据或大体积旧正文。**

## 规则

1. 旧 WP/规划文档只作历史参考，不是新设计或事实源；事实源是 `docs/dev/rebuild/06-*.md/csv`。
2. 实际文件保留在 `rebuild` 分支；新 worktree 不复制正文，只用 `git show rebuild:<path>` 查阅。
3. 需要复制进新分支时，先在临时目录脱敏并逐项确认；原始 auth/cookie/key、spikeQ4 证据不得入库。
4. 归档条目标注：原路径、日期、domain、敏感等级、是否已脱敏、替代文档。
5. `docs/BOTGUARD_VERIFICATION_CHAIN.md` 是 upstream 原生文件，grandfather：不复制、不改写、不纳入归档清单。

## 旧 WP / 规划清单

| 原路径 | 主题 | 处置 |
|---|---|---|
| `docs/dev/PLAN-ark-merge-20261017.md` | Ark 合并总计划 | 逻辑归档；正文留 rebuild |
| `docs/dev/PRE-P1-EXPERIMENTS-20261018.md` | P1 前实验 | 逻辑归档；正文留 rebuild |
| `docs/dev/SPIKE-RESULTS-20261017.md` | Spike 结果 | 逻辑归档；正文留 rebuild |
| `docs/dev/AUDIT-P2P3-20261018.md` | P2/P3 审计 | 逻辑归档；正文留 rebuild |
| `docs/dev/FRONTEND-MERGE-20261018.md` | 前端合并 | 逻辑归档；正文留 rebuild |
| `docs/dev/HANDOFF-NEXT-PHASE-20261020.md` | 阶段交接 | 逻辑归档；正文留 rebuild |
| `docs/dev/HANDOFF-WP5-UI-20261020.md` | WP5 UI 交接 | 逻辑归档；正文留 rebuild |
| `docs/dev/INSTANCE-SWITCH-SPEC-20261021.md` | 实例切换规格 | 逻辑归档；旧契约不作为实现依据 |
| `docs/dev/UI-CHANNEL-DESIGN-20261018.md` | UI/channel 设计 | 逻辑归档；channel 设计已 drop |
| `docs/dev/WP5-V3-DESIGN-20261019.md` | WP5 V3 设计 | 逻辑归档；正文留 rebuild |
| `docs/dev/WP5-V3-IMPLEMENTATION-PLAN-20261019.md` | WP5 V3 实现计划 | 逻辑归档；正文留 rebuild |
| `docs/dev/WP5-V4-DESIGN-20261020.md` | WP5 V4 设计 | 逻辑归档；正文留 rebuild |
| `docs/dev/WP6-STATS-PAGE-DESIGN-20261020.md` | 统计页设计 | 逻辑归档；统计后置 |
| `docs/dev/WP6-STATS-PAGE-DESIGN-BRIEF-20261020.md` | 统计页简报 | 逻辑归档；统计后置 |
| `docs/dev/WP7-UI-DESIGN-20261020.md` | WP7 UI 设计 | 逻辑归档；upstream 原生优先 |
| `docs/dev/WP7-V2-UI-SYNC-20261020.md` | WP7 V2 UI 同步 | 逻辑归档；upstream 原生优先 |
| `docs/dev/WP8-FORMAT-LAYER-PLAN.md` | 格式层计划 | 逻辑归档；协议后置 |

## 不归档

- `docs/BOTGUARD_VERIFICATION_CHAIN.md`（upstream 原生，grandfather）。
- `docs/dev/evidence/p3-build3/spikeQ4-evidence.json`、`spikeQ4.py`（敏感形状，只留 rebuild ref）。
