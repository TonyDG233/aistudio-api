# P4 决策问题：Build Surface / 9998 / Signer

P4 是 D07 原 43 行；已确认时新增 `D07-044`“失败现场截图 PNG + 页面 HTML dump”，总计 44 行。10 个问题已于 2026-10-25 确认；最终决定见 `06-decision-log.md` 的 P4。

| ID | 真正的问题 | 选项 | 涉及 feature IDs | 建议 |
|---|---|---|---|---|
| P4-Q1 | Build app 的默认来源怎么定？ | A 只从配置读，缺配置就报错；B 配置优先 + 保留 Ark 默认；C 继续硬编码 | `D07-001` | A |
| P4-Q2 | Build 页面复用还是每次重建？ | A 永远复用；B 每次重建；C 健康时复用，异常/换号/断线时重建 | `D07-002,003,042` | C |
| P4-Q3 | Build 的登录/BotGuard 怎么处理？ | A 参考项目移植 BotGuard 到 build 页；B 保留临时 chat bootstrap；C 不做 | `D07-004,005`; P3 BotGuard 行 | A |
| P4-Q4 | Native 9998 和 signer 谁主谁备？ | A native 主 + signer 首帧前回退；B 只用 signer；C 只用 native | `D07-012..017,019..021,043` | A |
| P4-Q5 | human gate / focus / 过场点击保留吗？ | A 全部保留；B 作为可选兼容层，默认按证据启用；C 全删 | `D07-006..009` | B |
| P4-Q6 | 9998 多实例怎么隔离？ | A 单端口 + 每实例 realm；B 每实例独立端口；C 不隔离 | `D07-010,011,018,030` | A |
| P4-Q7 | Build 页面允许多少并发请求？ | A 一个页面多请求；B 一页一请求，满了用其他 build 实例；C 可配置 | `D07-023..026,031` | B |
| P4-Q8 | build-only 的 Cookie 怎么回写？ | A 请求成功/关闭时回写，并允许手动重启；B 只在关闭时回写；C 不回写 | `D07-040`; `D03-028`; `D04-008` | A |
| P4-Q9 | Build 身份与配额归谁？ | A 用实例自己的 session/scope，配额来自实例配置；B 继续控制面 scope + 全局页配额；C 两者都保留 | `D07-038,039` | A |
| P4-Q10 | 非流式 Build 保留吗？ | A 保留；B 只保留流式；C 删除 | `D07-041` | A |

## 概念说明

- **Build app**：字节/Ark 的 AI Studio Build 页面，不是 chat 页面；需要 `app_id`/URL。
- **9998**：本地 WebSocket 桥，页面里的 app 客户端通过它接请求；`realm` 是同一端口下区分实例的命名空间。
- **signer**：页面内 JS 备用通道，负责采集请求头/warmup/状态机；native 不可用时使用。
- **BotGuard**：Google 的机器人检测/挑战机制；当前 chat 页使用，build 页没用。
- **AUTH_RESPONDER**：页面 boot 时回传 `authIndex` 的握手，缺了可能静默等待。
- **human gate / focus / 过场点击**：模拟真实鼠标、键盘、窗口焦点和页面弹窗，属于 UI/反自动化兼容层。
- **build-only**：只有 build 页、没有 chat 页的实例；当前靠临时 chat bootstrap 完成初始化。
- **Cookie 回写**：把 build 页刷新后的 Cookie 写回账号文件；当前 build-only 不走 `chat_ready`，所以不回写。
- **request_scope**：请求身份/在途计数的作用域；当前 build 仍记在控制面 session 上，可能和实例身份错位。

## 说明

- 本文件只列问题，不是已决策记录。
- 你按 `Q1 A, Q2 C, ...` 回复；确认后才写入 `06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`。
