# P5 决策问题：Generation / Protocol / Wire

P5 是 D06 的 69 行原表，追加 `D06-070` 官方 key 仅计数后共 70 行。问题已于 2026-10-25 确认；最终决定见 `06-decision-log.md` 的 P5。OpenAI Chat 是刚需；Claude 在 Build 后。

| ID | 真正的问题 | 选项 | 涉及 feature IDs | 建议 |
|---|---|---|---|---|
| P5-Q1 | OpenAI Chat Completions 是否作为硬需求保留？ | A 是，首批；B 是，但后置；C 不做 | `D06-012`, `D06-057` | A |
| P5-Q2 | Gemini / Claude / OpenAI Responses 还保留吗？ | A 四协议全保留；B Gemini+OpenAI 先，Claude/Responses 后；C 只做 Gemini+OpenAI Chat | `D06-004`..`D06-013`, `D06-058`, `D06-059` | B |
| P5-Q3 | 生成核心是否保持协议中立事件流？ | A 保留 StreamEvent + 各协议渲染；B 每协议一套核心；C 只保留 Gemini | `D06-001`..`D06-003`, `D06-014`, `D06-066` | A |
| P5-Q4 | ATG（Anti-Truncation Gateway）怎么处理？ | A 保留，默认 off，按协议/模型开启；B 默认 prefer；C 丢弃 | `D06-042`..`D06-053` | A |
| P5-Q5 | 流式首事件是否统一预读，以保留真实 HTTP 状态？ | A 四协议统一预读；B 仅 Claude/OpenAI/Responses；C 保持 Gemini 200 | `D06-007`, `D06-008`, `D06-010`, `D06-014` | A |
| P5-Q6 | tool/thinking/images/signature/usage 是否全部保留？ | A 全部保留；B 只保留文本+tool；C 按协议分级 | `D06-015`, `D06-016`, `D06-019`..`D06-028`, `D06-030` | A |
| P5-Q7 | chat 请求继续 capture/replay，还是改直接 HTTP？ | A 保留 capture/replay；B 直接 HTTP；C 混合 | `D06-028`..`D06-038`, `D06-068`, `D06-069` | A |
| P5-Q8 | countTokens 先近似还是做结构化精确计数？ | A 默认沿用 system/tools/content 统一打包的粗略计数；后续 UI 可选官方 API key 做精确计数；B 立即精确；C 不做 | `D06-054`..`D06-056`, `D06-070` | A |
| P5-Q9 | 非流式与空响应重试是否保留？ | A 保留非流式+同账号空响应重试；B 只流式；C 只非流式 | `D06-001`, `D06-004`, `D06-050`, `D06-051`, `D06-065` | A |
| P5-Q10 | raw dump、错误映射、feature gate 怎么处理？ | A 保留 raw dump（脱敏/按需）+错误映射+gate；B 只保留错误映射；C 全删 | `D06-061`..`D06-064`, `D06-067` | A |
| P5-Q11 | build REST body 是否在 P5 做？ | A P5 做；B 推到 P4 Build；C 复用 chat wire | `D06-039`..`D06-041` | B |

## 概念说明

- **协议中立核心**：生成服务只产出统一的 `StreamEvent`，由 Gemini/OpenAI/Claude/Responses 各自渲染。
- **capture/replay**：从 chat 页面抓一个请求模板，后续请求改写模板再回放，不是直接发普通 HTTP。
- **wire/JSPB**：AI Studio 内部的数组/二进制结构；`wire_codec/parser` 负责编码解码。
- **ATG**：Anti-Truncation Gateway，用合成 tool call 绕过截断；默认 off，模式 `off/prefer/required`。
- **首事件预读**：流式开始前先读第一个事件，能在未出正文时返回真实 HTTP 错误；Gemini 当前先出 200。
- **tool/thinking/images/signature**：函数调用、思考文本、图片、thought_signature 的跨协议映射。
- **countTokens**：当前把 system/tools/对话拼成单条文本近似计数，不是上游结构化计数。
- **raw dump**：把原始请求/响应落盘，调试用；必须脱敏、按需。
- **build REST body**：Build 面不走 chat capture，而是发 REST body；归 P4，不阻塞 chat 最小实现。

## 说明

- 本文件只列问题，不是已决策记录。
- 你按 `Q1 A, Q2 B, ...` 回复；确认后才写入 `06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`。
