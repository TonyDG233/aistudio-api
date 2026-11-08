# P7 决策问题：Logs / Stats

P7 是 D09 + D10 原 67 行共 67 行（51 条非原生）。问题已于 2026-10-25 确认；最终决定见 `06-decision-log.md` 的 P7。日志页先实现，统计页后置。

| ID | 真正的问题 | 选项 | 涉及 feature IDs | 建议 |
|---|---|---|---|---|
| P7-Q1 | 日志页与导出保留到什么程度？ | A 日志页 + 1/3/7 天导出；B 只做后端日志；C 删除日志页 | `D09-027`..`D09-032` | A |
| P7-Q2 | 日志存储怎么保留？ | A ring 5000 + 每日文件 7 天；B 只每日文件；C 只 ring | `D09-010`, `D09-012`, `D09-016`, `D09-025`, `D09-026`, `D09-033` | A |
| P7-Q3 | dump 原始请求/响应怎么处理？ | A 只落 `safe_*`，默认关，按需开启；B 保留 raw 但明确本地风险；C 删除 dump | `D09-021`, `D09-022` | A |
| P7-Q4 | 全量 DEBUG 噪音怎么处理？ | A root DEBUG + 命名空间/三方库过滤；B root INFO；C 不过滤 | `D09-004`, `D09-035` | A |
| P7-Q5 | 统计后端用什么模型？ | A 单一事件账本（request/attempt/usage/error），聚合派生；B 保留 attempt/client 双计数器；C 只保留日志 | `D10-001`..`D10-010`, `D10-016`, `D10-020`, `D10-025`, `D10-027` | A |
| P7-Q6 | 统计页怎么展示分母？ | A client 与 attempt 分区显示、绝不混算；B 合成一个成功率；C 只看 client | `D10-027`, `D10-028`, `D10-032` | A |
| P7-Q7 | 统计保留期与范围怎么定？ | A daily 90 天 + all-time；start/effective 明确；B 30 天；C 不做历史 | `D10-010`, `D10-022`..`D10-024` | A |
| P7-Q8 | countTokens 与 paid 元数据怎么处理？ | A countTokens 独立统计；paid 从模型目录读取；B 混入生成统计；C 删除 | `D10-020`, `D10-021`, `D10-025`, `D10-029`, `D10-031` | A |
| P7-Q9 | 日志/统计 API 形态？ | A 日志 SSE + 统计 REST 聚合；B 全部 SSE；C 全部轮询 | `D09-013`, `D09-018`, `D10-024` | A |

## 概念说明

- **ring 5000**：内存中的日志环形缓冲，SSE 从这里回放。
- **daily file**：按天落盘 `aistudio-YYYYMMDD.log`，默认保留 7 天。
- **dump**：把请求/响应原文写成 `req_*.json`，当前会写原始 headers/body，存在凭据泄露风险。
- **root DEBUG**：root logger 固定 DEBUG 捕获全量；三方库/CDP/uvicorn 噪音会一起进来。
- **attempt**：一次上游尝试；**client**：一个客户端请求；两者分母不同，不能混算成功率。
- **事件账本**：一条请求/尝试事件包含 request_id、attempt_id、account、model、surface、tokens、latency、error、paid；聚合从事件派生。
- **daily / all-time**：按日保留与历史总量；当前 `range.start` 和实际生效起点可能不一致。
- **countTokens**：独立计数，不应污染生成请求统计。
- **paid map**：由模型目录/发现服务拥有；统计页只消费。
- **token 汇总**：统计页已有突出汇总数；口径为 generation + input + cache 的集合，不含 countTokens。该能力归既有 `D10-030`/`D10-032`。

## 说明

- 本文件只列问题，不是已决策记录。
- 你按 `Q1 A, Q2 A...` 回复；确认后才写入 `06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`。
