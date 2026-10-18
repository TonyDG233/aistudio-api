# 直连通道切换 ProxyStreamedCall 传输（2026-10-17）

> 目标：让直连通道**零编译**支持任意请求形态（ANY/强制工具等）。
> 结论：已切换并真机验收通过（强制工具 200 + functionCall 原样返回；普通请求 200 + 文本；ATG 包装/解包链路同验）。

## 一、动机

- 旧直连 = `GenerateContent` + **位置数组编译**（只支持 AUTO；ANY/allowedFunctionNames 等本地 400）；
- 原生出站采样（见 `native-forced-tool-egress-capture-20261017.md`）证明：`ProxyStreamedCall`
  以 `[路径, 原始 JSON 字符串, 签名]` 原样透传，天然不存在"编译槽位"问题。

## 二、实现

1. 请求体：`[ "/v1beta/models/<model>:streamGenerateContent", task.body（原始 JSON 字符串）, token ]`。
2. **宿主签名方案（本轮实破）**：`token = M.Cn(tb, await M.GB(路径 + " " + 请求JSON字符串))`。
   - `M.GB` 输出 = 64 位十六进制摘要（SHA-256 形态）；`M.Cn` 输出 = `!` 前缀 token；
   - warmup 出站体 element[2] 与该 token 逐字一致（交叉验证）；
   - 取得方式：包装页内 `M.Cn`/`M.GB` 记录宿主 warmup 真实调用（0 额度），经 `signer_note` 通道回传。
3. 响应解码：ProxyStreamedCall 返回 **holder 形态**：

   ```
   [[[ "{\"candidates\":[...],\"usageMetadata\":{...},\"modelVersion\":\"gemini-3.8-flash\",...}",
       "[...下一块...]", ... ]]]
   ```

   即"REST chunk 作为转义 JSON 字符串、装在单元素数组里、再套两层数组"。
   - 逐帧扫描：holder 元素完成即双解析 → REST chunk 直接发帧（渐进流式）；
   - 兜底一：整包 `JSON.parse` 后提取单字符串 holder；兜底二：递归 walker（任意层字符串双解析）；
   - 旧编译 envelope（`wrapResponse`）保留为兼容分支；本地 400 仅剩"非法 JSON"。

## 三、验收（v24，`%TEMP%\kilo\direct-run24-holder.log`）

| 探针（fallback 关，全走直连）                     | 结果                                                     |
| ------------------------------------------------- | -------------------------------------------------------- |
| 强制工具（toolConfig ANY + allowedFunctionNames） | 200 + `functionCall emit_reply {"content":"OK"}`（原样） |
| 普通文本                                          | 200 + `text "OK"`（同时验证 ATG 包装与解包链路）         |

日志：两请求均 `task done ... frames=2 bytes≈2.4KB reads=2 dropped=0`。

## 四、过程节点（含 403 弯路）

- v20：首测 **403 PERMISSION_DENIED**——token 用了编译时代方案（内容文本摘要），错误；
- v21（0 额度诊断）：包装 `M.Cn`/`M.GB` 记录宿主 warmup 调用 → 得正确签名方案；
- v22：403 消失；新问题=响应解不出（holder 形态 + 兜底 catch 提前 return 不可达）；
- v23：walker 兜底 + 样本落盘（`%TEMP%\direct-signer-sample.txt`，2746 字节全文）；
- v24：holder 解码上线 → **验收通过**。

## 五、运行坐标

- v24：`bgp_14971a403001smmLc9FheK5lPv`（pid 29192），直连开、fallback 关
- 探针：`%TEMP%\kilo\forced-tool-probe.cjs`、`%TEMP%\kilo\sse-gemini-stamp.cjs`
- 诊断残留（debug 级、有界）：`signer_note`（Cn/GB 输入输出、warmup 出站体）、
  错误样本落盘 `%TEMP%\direct-signer-sample.txt`、NetWatch（Proxy\*Call/GenerateContent 出站记录）
