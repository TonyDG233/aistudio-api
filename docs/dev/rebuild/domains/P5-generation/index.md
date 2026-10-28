# P5 Generation / Protocol / Wire

状态：B 未开始。本文件是 P5 唯一域索引；详细技术分析只在 `09-pg-toolcall-analysis.md`。

## B5a 早期约束

- 复现“酒馆预设把发送文本放进 `functionResponse`、回答放进 `functionCall.args`”的 PG/toolcall/preset 链路。
- PG-01..PG-04 必须有离线复现/修复/回归；PG-05..PG-13 逐项 triage，标阻塞或后置。
- 预设形态 harness 必须通过。
- 只允许离线 fixture/probe；不得访问真实账号/IP/Google。
- B5a 未通过不得进入 B5b。

## B5b 早期约束

- 1 账号 + 1 Context + 1 Page，经新 account/page/routing/active_requests/guard/log seam。
- 1 个 OpenAI Chat 请求收到完整回复；空 API Key 不放行。
- 同账号仅 1 个 active Page；无 Page/Context 泄露；请求结束 `active_requests` 归零。
- OpenAI Chat 是上游没有、fork 新增的适配器，不能拿原生 Gemini/Responses 代替。

## 文档归属

- 步骤/退出条件：`05 §4.1`
- 决策门槛：`06-decision-log.md` D7
- 技术详情：`09-pg-toolcall-analysis.md`
- 交接指针：`10-handoff-prompt.md`
- 证据：`docs/dev/evidence/P5/`
- 不创建 `B5a.md`；不把 PG-01..PG-13 复制到 05/06/10。
