# 08-10 Build Surface 故障记录（2026-10-28）

状态：**事故记录，仅用于 P4 Build 实现阶段的前期探索**。当前没有进入 P4，不要求现在修复或溯源 Build。

## 1. 事故日志

用户提供的运行日志（整理摘录；时间精度/字段顺序按日志查看器为准；未独立核对原始日志）：

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
- 上游返回：`400 INVALID_ARGUMENT`。
- 明确错误码：`PROXY_GENERATION_DISABLED`。
- 明确原因文本：`streamGenerateContent is not supported`。
- 发生时机：native 在首帧前断开；不是已出正文后的中断。
- 当前处置：等待 `9998` 重连后重试；随后仍报 `NATIVE_ERROR`。
- 该错误不是网络超时、空 200、malformed SSE 或普通 BotGuard 失败。

## 3. 当前结论边界

- 根因**未确认**；不能只凭错误文本认定是端点、账号、tier、代理或签名问题。
- 不能据此断言 signer 可用或不可用。
- 不能把 `9998` 重连当作修复；该 400 可能是端点/渠道级拒绝，重连不会改变支持状态。
- `08-11-build-400-trace.md` 是辅助附录，不是 P4 门、不是当前任务、不要求补证据或签字。

## 4. P4 实现阶段的前期探索要求

等到 P4 Build 真正开始实现时，在写 native/transport/Q4 相关业务代码前，必须基于本文和当时的最新日志重新做一次前期探索：

1. 读取失败请求/日志，确认 URL、method、surface、app_id、model、请求体形状、关键 headers（脱敏）、native/signer 通道、账号/tier、代理/出口。
2. 重新确认 `streamGenerateContent` 是否仍是 Build 支持的端点；是否存在新的 Build REST/native WS/signer 入口。
3. 分别验证 native 与 signer；禁止 native 400 后无条件把 signer 当兜底。
4. 对明确 400/`PROXY_GENERATION_DISABLED` fail-fast 或有限回退；禁止在 9998 上无限重连。
5. 重新确认 Q4（native/signer 主备）并记录到 P4 阶段文档。
6. 做功能可行性重试和解决方案测试；无成功样本时明确“无基线”，不得伪造回归通过。

这属于 P4 阶段的前置探索，不是当前 Gate0/Gate0.5 的动作。

## 5. 当前状态

- P4 未开始；Build 不在当前修复范围。
- 事故已记录；不宣告 Build 渠道可用。
- 未访问真实账号/IP/Google。
