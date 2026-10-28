# P9 决策问题：Boot / Config

P9 是 D01 的 62 行（41 条非原生）；9 个问题已于 2026-10-25 确认；最终决定见 `06-decision-log.md` 的 P9。这块决定新骨架启动时装配什么、配置从哪里来、哪些旧路由配置必须删掉。

| ID | 真正的问题 | 选项 | 涉及 feature IDs | 建议 |
|---|---|---|---|---|
| P9-Q1 | FastAPI lifespan/启动/关闭怎么保留？ | A 保留 lifespan，但补启动/关闭测试；B 改成无 lifespan 的懒启动；C 不做生命周期 | `D01-001`..`D01-004`, `D01-018` | A |
| P9-Q2 | 启动预热顺序怎么定？ | A active 账号预热 → 实例 startup → 模型发现 → keepalive；B 全部懒启动；C 只 warmup active 账号 | `D01-008`..`D01-015` | A |
| P9-Q3 | 旧 rotator / groups / channel 配置怎么处理？ | A 删除 fork 的策略组/channel/打分配置，保留上游 rotator 的账号状态与 429 冷却；B 连 rotator 一起全删；C 全部保留 | `D01-006`, `D01-054`, `D01-058`..`D01-062` | A |
| P9-Q4 | 配置来源与优先级怎么定？ | A YAML + env + 默认值，单一 schema；B 只 env；C 保持多套重复键 | `D01-033`..`D01-037`, `D01-043`, `D01-044` | A |
| P9-Q5 | channel/surface 配置怎么表达？ | A 删除 channel registry，只保留 `surface=chat/build` + 实例配置；B 保留 channel；C 保留别名 | `D01-051`, `D01-058`..`D01-062` | A |
| P9-Q6 | 实例配置怎么迁移？ | A 每实例 `count/surface/min_tier`；B 继续 channel/max_pages；C 继续 max_contexts | `D01-049`..`D01-053` | A |
| P9-Q7 | 配置热更新与原子写回保留吗？ | A 保留热更新 + 原子写；B 只重启生效；C 不允许写配置 | `D01-049`, `D01-050` | A |
| P9-Q8 | legacy 配置键怎么处理？ | A 一次性迁移后删除旧键；B 永久双读；C 直接删除不迁移 | `D01-057` | A |
| P9-Q9 | 启动错误策略怎么定？ | A 关键配置 fail-fast，非关键回退默认；B 全部回退；C 全部 fail-fast | `D01-034`..`D01-036`, `D01-042` | A |

## 概念说明

- **lifespan**：FastAPI 启动/关闭生命周期；当前 boot 顺序在 `app.py:54-267`。P9-Q1/Q2 保留 upstream 的 lifespan 骨架和清理顺序，只替换装配清单（manager/pool/keepalive）并补测试，不重写生命周期机制。
- **warmup**：启动时先拉起 active 账号浏览器；不是账号路由。
- **channel registry**：旧 `pg/build` 渠道分类；新核心只保留 `surface=chat/build`，并落到实例配置。
- **features 归一化**：把 YAML/env/defaults 合并成运行时配置；当前包含 groups/slots/routing 等旧策略。
- **legacy keys**：`max_realms`、`global_max_instances`、`max_page_instances` 等旧键。
- **热更新**：运行期改 `config.yaml` 后原子写回，不重启生效。
- **fail-fast**：关键配置非法时启动直接失败，而不是静默用默认值。

## 说明

- 本文件只列问题，不是已决策记录。
- 你按 `Q1 A...` 回复；确认后才写入 `06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`。
