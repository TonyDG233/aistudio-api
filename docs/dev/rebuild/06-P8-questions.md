# P8 决策问题：API / UI

P8 是 D11 的 42 行，全部非原生；9 个问题已于 2026-10-25 确认；最终决定见 `06-decision-log.md` 的 P8。API 先保证 OpenAI/Gemini；Claude 在 Build 后；UI 只做最小可持续运维面。

| ID | 真正的问题 | 选项 | 涉及 feature IDs | 建议 |
|---|---|---|---|---|
| P8-Q1 | 协议 API 的实现顺序？ | A OpenAI Chat + Gemini 先；Claude 后；Responses 更后；B 四协议同批；C 只 OpenAI | `D11-001`..`D11-011` | A |
| P8-Q2 | 模型列表契约怎么定？ | A 裸模型 ID 默认，只有双渠道实际启用且分流时才加前缀；B 永远裸 ID；C 永远带 pg/build 前缀 | `D11-004`, `D11-005`, `D11-007` | A |
| P8-Q3 | API 鉴权怎么处理？ | A 默认要求 Key，显式配置才允许空 Key；B 保持无 Key 全开放；C 只允许 loopback 免 Key | `D11-015`..`D11-018`, `D11-035`, `D11-036` | A |
| P8-Q4 | 错误映射怎么保留？ | A 保留协议感知错误体 + 明确 HTTP 契约；B 全部统一 500；C 直接透传上游 | `D11-013`, `D11-014` | A |
| P8-Q5 | 最小 UI 包含哪些页？ | A 登录/总览/账号/实例/日志/设置；统计后置；B 只日志+设置；C 全部页面一次做 | `D11-022`..`D11-034`, `D11-037`..`D11-039`, `D11-042` | A |
| P8-Q6 | 实例/账号 UI 怎么表达？ | A 一实例=账号+Context+Page+surface；显示手动切换/重启；B 保留账号+channel 矩阵；C 只显示账号列表 | `D11-029`..`D11-031`, `D11-039`, `D11-040` | A |
| P8-Q7 | 旧切号/旧实例 UI 怎么处理？ | A 删除 `/rotation/next`、孤儿组件、渠道矩阵；B 保留但隐藏；C 全部保留 | `D11-021`, `D11-041` | A |
| P8-Q8 | API Key/控制台密码 UI 安全怎么定？ | A 密钥只显示掩码，登录用服务端会话；B 保留明文显示+localStorage token；C 不做 UI 管理 | `D11-018`, `D11-035`, `D11-036` | A |
| P8-Q9 | 协议开关与示例怎么显示？ | A 开关按实际能力；示例随 features 隐藏；B 四协议示例常显；C 不做示例 | `D11-012`, `D11-037` | A |

## 概念说明

- **裸模型 ID**：`gemini-...`，不带 `pg/`、`build/` 前缀。
- **条件前缀**：只有两个渠道都启用且实例分配分流时，API 才暴露前缀；否则默认裸 ID。
- **API Key**：请求侧鉴权；当前无 Key 会直接放行。
- **控制台密码**：Web UI 登录；和 API Key 是两套。
- **服务端会话**：登录 token 放 HttpOnly cookie，而不是 localStorage。
- **最小 UI**：先覆盖登录、总览、账号、实例、日志、设置；统计页后置。
- **旧切号**：`/rotation/next` 与 `forceNextAccount`，当前 UI 无调用方。
- **渠道矩阵**：旧 `AccountChannelMatrix`/槽位绑定 UI，和一实例一 surface 冲突。

## 说明

- 本文件只列问题，不是已决策记录。
- 你按 `Q1 A, Q2 A...` 回复；确认后才写入 `06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`。
