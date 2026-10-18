# 客户端绕行检测 + Gemini 入口 ATG 接线 — 运行记录（2026-10-17）

分支：`feat/makersuite-direct-signer`（未提交）
运行实例：`MAKERSUITE_DIRECT=on`，ATG 走 `.env`（`ATG_MODE=prefer`）；端口 8889。
日志：`%TEMP%\kilo\direct-run8-seg.log` / `direct-run9-guard.log` / `direct-run10-guard2.log`

## 一、分段调用模板实验（run8，已回退）

- 机制记录：模型在**单次生成**中填写多份工具调用（同一响应内多个 functionCall part），
  非五次独立生成；服务端对每个函数调用完成即投递（原子粒度）。
- 观测：500 字散文 → 5 个 delta、3 个到达时段（+14.38s / +14.69s / +15.36s）。
- 裁定（用户）：工具调用无法做到 token 级流式，放弃该路线；分段模板代码已完全回退。

## 二、Gemini 原生入口接入 ATG（run9，验证 #1）

请求：`POST /v1beta/models/gemini-3.8-flash:streamGenerateContent`（无客户端工具）

```
[DEBUG] [ATG] wrapped (gemini): tool=atg_emit_e8fc271186c59c866e40917d, model=gemini-3.8-flash, contents=2, tools=1
[INFO]  [DirectSigner] task done request=req_...738950_v20mmrqw2 frames=2 bytes=964
```

客户端输出：`STATUS 200`、`ev#1 text "OK"`、`textChars=2`、`functionCalls=0` —— 正文为解包后的纯文本。
同日 07:05:31，一条真实的 27 轮外部请求（酒馆侧）同样出现 `[ATG] wrapped (gemini): tool=atg_emit_e8fc...,
contents=27, tools=1` 并正常完成（frames=2）。

## 三、客户端绕行检测 + 原生回退（run10，验证 #2）

请求：同入口，携带客户端自供工具 `emit_reply`（content:STRING）+ `toolConfig.functionCallingConfig
{mode:"ANY", allowedFunctionNames:["emit_reply"]}`。

```
[INFO] [ATG] client synthetic transport detected (gemini): tool=emit_reply, reason=forced-content-tool;
       local wrap skipped for this request
[INFO] [DirectSigner] toolConfig requires the native path (mode=ANY); request stays on build.js
[INFO] [Context#7] Received request: POST /v1beta/models/gemini-3.8-flash:streamGenerateContent
```

客户端输出：`STATUS 200`、`ev#1 functionCall name=emit_reply args={"content":"OK"}`、`functionCalls=1`
—— 本地 ATG 未叠加；请求经原生路径由客户端自己的机制处理，0 本地包装、0 400。

（run9 首跑同一用例时本地直接 400：codec `buildBody` 仅支持 AUTO 工具选择；已通过上文的
"toolConfig 需原生 → 直连退回" 修复，run10 复跑 200。）

## 四、实现清单（本次）

- 新增 `src/atg/clientPatterns.js`：五类信号（前缀名 / 声明意图+content 参数 / 强制固定工具 /
  历史 functionCall+functionResponse 环 / 指令文本），命中返回 `{hit,name,reason,tools}`；
  未命中但存在工具时返回 `declared-tools-present` 供日志分析。
- `injector.js`：`clientSyntheticGuard`（默认开，`ATG_CLIENT_SYNTHETIC_GUARD=off` 可关）命中即跳过本地包装；
  回退分段模板；保留 clientTools 透传。
- `AtgHandler._applyAtgWrap`：命中日志（info）与近失配工具名日志（debug）。
- `GeminiHandler`：请求包装 + 流式逐块解包 + 非流解包（上一轮接线，本轮验证通过）。
- `DirectSignerService.maybeHandleTask`：toolConfig 超出 codec 能力（mode!=AUTO / allowedFunctionNames）
  时主动退回原生路径。
- 测试：`test/atg/clientPatterns.test.js`（11 用例新增）；ATG 全量 97/97 通过；`npm run lint` 全绿。
