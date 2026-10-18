# ark-reference：Ark_AIStudioToAPI 移交资料索引

> 来源：`D:\LLM\Ark_AIStudioToAPI`（GitHub: TonyDG233 侧 Node 项目）· 移交日期：2026-10-18
> 目的：本目录是 Ark 项目为 aistudio-api 侧准备的**技术点移交包**，避免移植工作时重复逆向
> （Build 通道、直连签名器、bundle 考古等均为 Ark 侧已完成的一手研究）。

## 文件清单

| 文件 | 内容 | 建议读者 |
|---|---|---|
| `ARK-TECHNICAL-REFERENCE.md` | **技术总参考**：架构、请求生命周期、双通道（直连/原生）、凭证与账号、ATG、多格式层、开关总表、红线契约、移植对照表 | 所有人，先读这个 |
| `VPS-DEPLOYMENT-NOTES.md` | 用户"小鸡"（2G 内存 VPS）部署情况与硬约束（脱敏版）：资源预算、WARP 出网链、历史踩坑 | 做 Spike D / WP5 配额 / 部署方案时 |
| `build-channel/` | Build 通道 + 直连签名器原始研发文档（决策手册、方案、桥接史、封档/复活记录） | 做 WP4 时精读 |
| `build-channel/evidence-20261017/` | MakerSuite bundle 考古证据（方法名录、wire 位置交叉验证、`M.Cn/M.GB` 签名方案、token 样本）+ 可复现脚本 | 做 WP4 时精读 |
| `misc/create-ai-studio-app.md` | Build App 创建教程（Remix 空白应用、贴 `build.js`、公开分享、`AI_STUDIO_APP_URL`） | Spike A 环境准备 |

## 阅读顺序建议

1. 本目录 `ARK-TECHNICAL-REFERENCE.md` §1–§5（全局认知）；
2. 计划书 `docs/PLAN-ark-merge-20261017.md`（WP0–WP9 分工）；
3. WP4 开工前：`build-channel/transport-decision-and-400-triage.md` + `direct-signer-pipeline-20261017.md`
   + `evidence-20261017/README.md` -> `proxystreamedcall-transport-switch-20261017.md` -> `native-forced-tool-egress-capture-20261017.md`；
4. WP2 开工前：`ARK-TECHNICAL-REFERENCE.md` §7（ATG）+ Ark 仓库 `src/atg/*` 源码；
5. Spike D / 部署方案：`VPS-DEPLOYMENT-NOTES.md`。

## 脱敏与许可说明

- 本目录文档**已脱敏**：无账号 cookie 值、无 API key 全文、无服务器完整 IP/域名/节点端口；
  文中出现的 key/token 均为掩码或截断样本。
- 授权与出处（合并/发布提交应保留）：
  - 本目录文档来自 Ark_AIStudioToAPI，随该仓库一并按上游惯例开放引用；
  - ATG（Anti-Truncation Gateway）逻辑源自 [Xeltra233/Antigravity-gateway](https://github.com/Xeltra233/Antigravity-gateway)（MIT）；
  - Build 通道修复结论交叉引用了 `yunx738/ais2api`（社区修复）；
  - 本仓库自身谱系：`chrysoljq/aistudio-api`（原仓，MIT）→ `DeconstructedCube/aistudio-api` → `TonyDG233/aistudio-api`。
- 文档为 **2026-10-17/18 快照**；Ark 仓库是这些内容的活水源（line 号/行为可能随其演进变化）。

## 与计划书的对应关系

| 计划书 WP | 主要依据文档 |
|---|---|
| WP0 Spike A（凭证保真/Build 宿主） | 本目录 `VPS-DEPLOYMENT-NOTES.md`、`misc/create-ai-studio-app.md` |
| WP0 Spike B/C（签名器/直连流） | `build-channel/direct-signer-pipeline-20261017.md`、`evidence-20261017/*` |
| WP0 Spike D（内存测绘） | `VPS-DEPLOYMENT-NOTES.md` |
| WP2 ATG 移植 | `ARK-TECHNICAL-REFERENCE.md` §7 |
| WP3 账号刷新 | `ARK-TECHNICAL-REFERENCE.md` §5 |
| WP4 Build 面 | `build-channel/*` 全部 |
| WP8 多格式层 | `ARK-TECHNICAL-REFERENCE.md` §8 |