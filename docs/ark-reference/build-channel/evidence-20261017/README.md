# MakerSuite Bundle 考古证据（2026-10-17）

> 本目录保存 2026-10-17 凌晨"bundle 考古"行动的提取结果与可复现脚本。原始 JS 包（约 8MB）不入库，留存于本机 `.local_logs/evidence-makersuite-20261017/` 及临时目录，可用下述脚本重新采集。

## 方法

独立启动 Camoufox（storageState=auth-7，代理同主系统；**未触碰运行中的主实例、零生成请求、零额度消耗**），导航至 AI Studio 应用页 → 点击 Continue → 等待懒加载完成 → 采集 `performance` 全量资源 → 拉取 `boq-makersuite` 主包离线深挖。

## 文件清单

- `bundle-probe4.cjs` — 采集脚本（列出资源、下载含关键字的 JS 至本地）。
- `bundle-analyze.cjs` — 第一轮提取：方法名录、关键字符串上下文（`analysis.txt`）。
- `bundle-analyze2.cjs` — 第二轮：`serverStreaming` 传输、`oFa/mFa`、`zFa`、perUserQuota（`analysis2.txt` / `analysis2b.txt`）。
- `bundle-analyze3.cjs` — 第三轮：`tl/zu/Mv/aya` 字段映射（`analysis3.txt` / `analysis3b.txt`）。
- `analysis*.txt` — 各轮提取结果（全部为代码片段，无凭据）。

## 关键结论快照（详见 analysis 文件）

1. **方法名录约 250 个**（`analysis.txt` 首节）：含 `GenerateContent`、`ProxyUnaryCall/ProxyStreamedCall`、`GenerateAccessToken`、quota 系列、agent/interaction 流式系列等。
2. **`GenerateContent` 为 server-streaming RPC**：页面代码 `_.Hw.prototype.generateContent → this.A.serverStreaming("/$rpc/.../MakerSuiteService/GenerateContent", …)`（`analysis.txt`）——参考实现与我们的桥当前以 unary `response.text()` 读取，即"缓冲式"根源；真流式上游原生支持。
3. **线上 wire 位置交叉验证**（`zu` 请求类，`analysis3.txt`）：`model@1`、`systemInstruction@6`、`tools@7`、`token@5`（与页面例程 `_.l(c,5,d)` 一致）——与我们现役 codec 的顶层编号完全吻合。
4. **新发现**：`zu.setApiKey@15` / CountTokens 请求 `setApiKey@4`——wire 请求体内置 API Key 字段（当前我们走 header `x-goog-api-key`）。
5. **`candidateCount` 在全部 bundle 中零命中**：内部协议不存在该字段——`=1` 丢弃为最终语义，`>1` 无法支持（非猜测）。
6. `tl`/`Eya`（`analysis3.txt`）按字段号列举 `temperature/topP/topK/seed/thinkingBudget/mediaResolution/responseModalities/…` 等——**注意**：其编号属 UI 设置消息（`Sk`），与线上 wire 的 `generationConfig` 编号不是同一套，扩充 codec 时不得直接套用，须以实发样本/差分探针为准。
7. 其它：`_.Bya` = 向 generlang 剥离 `x-goog-api-key` 的页面机制；旧代理消息字段 `nFa=[1,3]`；`zFa` = 附加 tier 元数据的方法集合。
