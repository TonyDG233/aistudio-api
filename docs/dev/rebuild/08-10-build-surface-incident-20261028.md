# 08-10 Build Surface 故障记录（2026-10-28）

状态：**事故记录 / P4-B0 前置门**。本文不判断根因、不宣告 Build 可用、不替代 P4 方案。门拆为两段：**P4-B0a 实现前**完成 §4 的离线/fixture 溯源并产出 `08-11-build-400-trace.md`；**P4-B0b 实现后**完成 §5 的解决方案测试并进入 P4 验收。

## 1. 事故日志

用户提供的运行日志（整理摘录；时间精度/字段顺序按日志查看器为准，未添加或删改错误文本）：

```text
2026-10-28 12:04:21.000
WARN
aistudio.build_surface

[build] native 在首帧前断开，等待 9998 重连后重试（Proxy browser error: Google API returned error: 400 INVALID_ARGUMENT {"error":{"code":400,"message":"PROXY_GENERATION_DISABLED: streamGenerateContent is not supported","status":"INVALID_ARGUMENT"}}）

2026-10-28 12:04:22.000
ERROR
aistudio.upstream.errors

未知严重异常: [build_surface:stream] NATIVE_ERROR: Proxy browser error: Google API returned error: 400 INVALID_ARGUMENT {"error":{"code":400,"message":"PROXY_GENERATION_DISABLED: streamGenerateContent is not supported","status":"INVALID_ARGUMENT"}} | 处置建议: 查看原生 WS/signer 上报的错误；必要时重跑 ensure_ready
```

## 2. 已确认事实

- 失败面：`build_surface` / `surface=build` 的 native 流请求。
- 上游返回：HTTP/业务错误 `400 INVALID_ARGUMENT`。
- 明确错误码：`PROXY_GENERATION_DISABLED`。
- 明确原因文本：`streamGenerateContent is not supported`。
- 发生时机：native 在首帧前断开；不是已出正文后的中断。
- 当前处置：等待 `9998` 重连后重试；随后仍报 `NATIVE_ERROR`。
- 该错误不是网络超时、空 200、malformed SSE 或普通 BotGuard 失败。

## 3. 初步判断（待溯源验证）

`PROXY_GENERATION_DISABLED: streamGenerateContent is not supported` 更像**端点/渠道级拒绝**，不是 `9998` 瞬时断线：

- 不能假定单纯重连 `9998` 会改变上游对 `streamGenerateContent` 的支持状态；必须用端点/tier/账号/代理证据验证。
- 不能把该 400 当成瞬态错误无限重试；否则只会形成 native 断线→9998 重连→再次 400 的循环。
- 不能据此断言 signer 一定可用；signer 是否走同一上游端点、是否同样被禁，必须单独验证。
- 不能继续把 P4-Q4 的“native 主 + signer 首帧前回退”当成已验证事实；该假设必须重新溯源。

## 4. P4-B0a：实现前必须重新溯源（离线/fixture）

P4 Build 实现 agent 在写 native/transport/Q4 相关业务代码前必须完成并记录到 `docs/dev/rebuild/08-11-build-400-trace.md`；该报告由独立 reviewer 签字后才解除 B0a。报告必填：来源（用户日志/脱敏请求/代码）、时间点、URL、method、surface、app_id、model、请求体形状、关键 headers 的脱敏字段名、native/signer 通道、账号/tier、代理/出口、错误分类、端点结论、Q4 重审结论、未验证项。

1. **读取失败请求全貌**：优先使用用户提供的日志/脱敏请求/现有 fixture；**不得主动访问真实账号/IP/Google**；若确需实机抓包，必须由用户明确授权并记录授权范围。
2. **确认端点语义**：对比当前上游代码/文档/fixture，确认 `streamGenerateContent` 是否是 Build 支持的端点；是否存在新的 Build REST/native WS/signer 入口。
3. **区分拒绝层级**：判断 `PROXY_GENERATION_DISABLED` 是账号、tier、app、代理/出口、区域、端点废弃还是产品开关导致；不得只凭错误文本猜测。
4. **分别验证 native 与 signer**：两条链路独立测试；禁止 native 400 后把 signer 当无条件兜底。
5. **检查 9998/AUTH_RESPONDER/BotGuard/signer**：确认 9998 重连、handshake、首帧、signer 上报是否只是症状；不要把重连成功当成生成成功。
6. **建立失败分类**：端点禁用、账号/tier 不允许、签名/鉴权失败、代理策略、真实瞬时断线必须可区分。
7. **写回 Q4**：把 native/signer 的重新结论写回 `06-P4-questions.md` 与 `06-decision-log.md`；未写回前不得宣称 Q4 已重审。
8. **禁止无限重试**：对明确的 400/`PROXY_GENERATION_DISABLED` 必须 fail-fast 或有限回退；不能在 9998 上循环。

## 5. P4-B0b：实现后的解决方案测试与验收

P4 实现完成后、进入 P4 验收前必须基于当前报错完成：

- native 400 `PROXY_GENERATION_DISABLED`：明确失败、无无限 9998 重连、无首帧前反复重试。
- signer-only：在相同条件下单独验证；成功必须给出首帧/完成证据，失败必须给出同等级错误分类。
- native 与 signer 切换：只在有明确证据时回退；禁止把 400 当瞬态错误回退后再次循环。
- 错误透传：客户端收到可理解的 Build 错误，不伪装成 200 SSE、空流或普通 500。
- 回归：保留现有成功样本作为 fixture；**若当前没有成功样本，必须明确写“无基线”并补 native/signer 成功 fixture**，否则不得宣称回归通过。不得用真实账号/IP/Google 做无授权探测。
- 验收证据：脱敏请求/响应、日志、测试命令、结果、失败分类。

## 6. 对现有计划的影响

- `06-P4-questions.md` 的 P4-Q4 旧结论（native 主 + signer 首帧前回退）**暂停作为实现依据**。
- `06-decision-log.md` 的 P4 计划仍保留为历史范围，但加入 `P4-B0a/B0b` 事故门；B0a 未完成前不进入 native/transport/Q4 相关实现。
- `05` 的移植顺序中，P4 必须改为“先完成 B0a 溯源，再实现，再做 B0b 测试”。
- `10` 的 handoff 提示词必须要求 Build agent 先读本文、提交 `08-11-build-400-trace.md` 并完成 Q4 写回。
- `12` 的 Build 验收必须增加“400 端点禁用”与“9998 无循环重连”用例；这些是 P4 门，不是 Gate0.5 退出项。
- `13` 增加 P4-B0a/B0b 状态；不得把 P4 写成已可开工。

## 7. 当前状态

- 事故已记录，根因未确认。
- **P4-B0a 未完成**：native/transport/Q4 相关实现阻塞；signer-only 离线 spike 与非传输 P4 工作可继续。
- **P4-B0b 未执行**：实现后验收测试尚未开始。
- 不宣告 Build 渠道可用，也不宣告 signer 可用。
- 未访问真实账号/IP/Google；本文只基于用户提供的日志与现有代码/文档。
