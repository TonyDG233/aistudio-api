# 原生链路强制工具请求体捕获（2026-10-17）

> 目的：获取"toolConfig ANY + allowedFunctionNames"请求在**原生通道**的真实出站形态，
> 为直连编解码器（GenerateContent 位置数组）的 ANY 槽位对齐提供地面样本。
> 结论先行：**原生链路根本没有"编译"这一步——原始 Gemini JSON 字符串原样透传**。
> "抓原生样本补编解码器槽位"这条路被证伪（可抓对象不存在）。

## 一、采样手段（有效的那个）

运行 v18（`MAKERSUITE_DIRECT=on` + `MAKERSUITE_DIRECT_FALLBACK=on`），在
`BrowserManager` 上下文创建处挂 **Playwright `context.on("request")` 监听（NetWatch）**：
命中 `MakerSuiteService/(Proxy*Call|GenerateContent)` 或请求体含
`streamGenerateContent|toolConfig|allowedFunctionNames` 时，记录方法、URL、发起 frame、请求体（截断 6KB）。

**负结果（已试过且无效，勿重蹈）**：

1. 顶层页面 XHR/fetch 原型钩子（DirectSigner 页面脚本内）——warmup 吞咽走它有效，但**真实生成请求不经过它**；
2. 非顶层框架探针（page.addInitScript + postMessage 中继）——未捕获到任何真实出站；
3. 各框架 performance resource 快照——只能看到 gstatic 资产噪音。

结论：该浏览器里真实生成出站**绕过页面 JS 原型补丁**，只有 Playwright 网络事件层（NetWatch）看得到。

## 二、捕获原文（run18，2026-10-17T08:54:34.317Z，探针 = 强制工具 emit_reply）

```
[NetWatch] a#7 POST xhr https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ProxyStreamedCall
frame=https://aistudio.google.com/apps/56128c84-c8e6-4d52-bf94-934c836720d9?showAssistant=true&showPreview=true
```

请求体（3403 字节，**三段式数组**；签名块中段截断）：

```
["/v1beta/models/gemini-3.8-flash:streamGenerateContent",
 "{\"contents\":[{\"parts\":[{\"text\":\"只输出两个字符：OK\"}],\"role\":\"user\"}],\"toolConfig\":{\"functionCallingConfig\":{\"allowedFunctionNames\":[\"emit_reply\"],\"mode\":\"ANY\"}},\"tools\":[{\"functionDeclarations\":[{\"description\":\"Transport channel used to deliver replies.\",\"name\":\"emit_reply\",\"parameters\":{\"properties\":{\"content\":{\"type\":\"STRING\"}},\"required\":[\"content\"],\"type\":\"OBJECT\"}}]}],\"safetySettings\":[{\"category\":\"HARM_CATEGORY_HARASSMENT\",\"threshold\":\"OFF\"},{\"category\":\"HARM_CATEGORY_HATE_SPEECH\",\"threshold\":\"OFF\"},{\"category\":\"HARM_CATEGORY_SEXUALLY_EXPLICIT\",\"threshold\":\"OFF\"},{\"category\":\"HARM_CATEGORY_DANGEROUS_CONTENT\",\"threshold\":\"OFF\"}]}",
 "!UVKlUjbNAAYkRIWRBERCFlhtOU62BvI7AEABEArZ1DwydeVV6BPOcu1wpuUALRAnaDg5T7aNwI9wQIv1On…（截断，共 ~2.2KB base64url 风格签名/凭据块）…yM-ZAv75cT5BeTm74LmD6gG"]
```

## 三、结论

1. **原生出站 = `[原始路径, 原始 Gemini JSON（字符串）, 签名块]` 发往 `MakerSuiteService/ProxyStreamedCall`**。
   `toolConfig ANY + allowedFunctionNames` 在 element[1] 里**逐字原样**，没有任何位置数组编译。
2. 因此本项采样目标里的"ANY 编译槽位"**在原生链路中不存在**；直连编解码器（GenerateContent 位置数组）
   的非 AUTO 槽位**无法从原生流量获得样本**——原"样本驱动补槽位"方案不可行（负结论，归档备用）。
3. **价值更大的替代路线浮出**：直连签名器改用**同款 ProxyStreamedCall 传输**（页面脚本直接发
   `[path, JSON.stringify(请求体), 签名块]` + 收割的凭据头），即可**零编译地支持任意请求形态**
   （ANY/任意 toolConfig/多 candidate/任意 thinkingLevel……），潜在消除整套编码器能力边界。
   待解决：① 签名块生成（疑似与 `M.Cn`/`M.GB` 同族，需采样对齐）；② ProxyStreamedCall 响应流
   与现有双包裹解码器的兼容性验证；③ 真机验收（1–2 次调用）。
4. 现状不受影响：强制工具请求继续走"可选自动改道原生"开关（已在 v15–v18 连测 4 次均为 200、
   函数调用原样返回），或保持默认显式 400。

## 四、运行坐标

- 运行：v18，`bgp_1490fe600001Euz7vY54k7whKA`（pid 18080），日志 `%TEMP%\kilo\direct-run18-netwatch.log`
- 探针：`%TEMP%\kilo\forced-tool-probe.cjs`（emit_reply + toolConfig ANY，0 前置直连成本，原生 1 次真实调用）
- 采样代码：`src/core/BrowserManager.js` 上下文创建处的 NetWatch 监听
