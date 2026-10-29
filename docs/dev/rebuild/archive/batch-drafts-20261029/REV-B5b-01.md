# REV-B5b-01 openai seam review

- 被审包：`docs/dev/rebuild/domains/P5-generation/packages/PKG-B5b-01-openai-seam.md`
- reviewer：只读 agent
- 状态：CONDITIONAL
- 基线：目标 worktree `.kilo/worktrees/rebuild-upstream` HEAD `755e28a`（`99c38ba` + `9b5cdea`）
- 核对方式：只读文件与行号核对；未运行测试，未修改被审包。

## 结论

包明确不走 Gemini/Responses 原生路径冒充 OpenAI，真实测试授权与回滚要求也清楚；但 B5b 的核心 seam `active_requests` 尚未冻结，§3 写“B2 已交付”与 §8 写“API 名称/返回形状未冻结”互相矛盾，且 B4 日志关联尚未交付。上述依赖未闭合，加上目标 worktree 无 `.venv`、§5 命令不能直接执行，本包应判 CONDITIONAL，需在 B2/P3 冻结与 B1/B4 前置完成后才能批准。

## 发现

| 编号 | 严重度 | 文件:行 | 事实 | 影响 |
|---|---|---|---|---|
| B5b-01 | 中 | `PKG-B5b-01:40,78` | §3 写“B2 已交付 account/page/routing/active_requests seam”，§8 又写“B2 最终 `active_requests` API 名称/返回形状未冻结；按 B2 包接口适配，不能拿 `_in_flight` 直接冒充”。目标 `session.py:136-147` 确为 `_in_flight`（`request_scope`），B5b 的 `test_openai_chat_seam.py` 直接依赖 acquire/release 形状。 | B5b 无法按当前文本确定调用接口与测试断言；在 B2/P3 冻结前不能批准。 |
| B5b-02 | 中 | `PKG-B5b-01:40-41` | §3 把“B1 已交付 API guard”“B2 已交付 seam”“B4 已交付日志关联”写成既成事实；B1/B2/B4 包当前均为 draft，B4 日志关联/`X-Request-ID` schema 尚未落地。§6 的停止条件只列 B5a 未通过、B1 guard 未落地，未列 B2/B4。 | 前置状态被高估；若 B4 schema 未按 B5b 预期冻结，`request-attempt` 关联测试无法成立，且停止条件不覆盖。 |
| B5b-03 | 中 | `PKG-B5b-01:62` | §5 离线命令使用 `.venv\Scripts\python.exe`；目标 worktree `.kilo/worktrees/rebuild-upstream` 无 `.venv`（主工作区有），README 的测试命令为 `uv run pytest`（`README.md:411`）。 | 命令在目标 worktree 直接执行会失败，JUnit 证据不可按包复现。 |
| B5b-04 | 低 | `PKG-B5b-01:16,42` | 范围内要求“请求-尝试日志关联”，依赖 B4 的 `request_id`/`attempt_id` schema；B4 包写 schema 需在 B4 开始前冻结（`PKG-B4-01:31`）。 | B5b 的日志关联断言依赖未冻结的外部契约；与 B5b-02 同源。 |
| B5b-05 | 低 | `PKG-B5b-01:64` | 真实命令写“Key 从环境变量读取”，未给变量名；临时 `AISTUDIO_CONFIG_FILE`/`AISTUDIO_DATA_DIR` 已给，但未说明 1 个测试账号如何准备。 | 真实测试启动步骤不完整；需用户指定账号/tier/模型（§8 已提）。 |
| B5b-06 | 低 | `PKG-B5b-01:26` | 包写 `routes_openai.py`“首事件预读复用 B5a 契约”；B5a 的 PG-02 修改在 Gemini handler（`api_service_gemini.py`），目标树没有 `routes_openai.py` 可复用的既有实现。 | “复用 B5a 契约”是模式复用而非代码复用，表述易被理解为共享同一实现。 |

## 验收门槛

- 范围是否闭合：否。`active_requests` API 未冻结，B1/B2/B4 前置状态未闭合。
- 文件域是否可并行：部分。新增 adapter/route/service 文件清晰；`api/app.py` 与其它协议路由包共享已标冲突。
- 测试是否可执行：部分。离线 fake seam 用例可写；依赖 `active_requests` 形状；目标 worktree 无 `.venv`。
- 证据是否可复现：部分。JUnit 可生成；真实测试命令本任务不执行且需授权，Key 变量名/账号准备未定。
- 失败停止/回滚是否明确：是。§6 有 Page/Context 泄露、`active_requests` 非 0、未授权真实请求等停止点；§7 有 Page/Context 清理与临时 data dir 回滚。
- 是否误用旧 fork 路径或 534 当任务：未把 534 当任务；`format_converters/openai_*`、`routes_openai.py` 已明确标为 fork 参考而非目标树既有实现；未用原生路径冒充 OpenAI。

## 给用户的裁决项

1. B2/P3 `active_requests` 的 API 名称/返回形状何时冻结；B5b 是否等冻结后再批准，而不是按“B2 已交付”推进？
2. B1/B4 前置未交付时，B5b 是否仅保持 draft；是否把 B2/B4 加入 §6 停止条件？
3. B5b 是否只做单次 fail-fast、不复制 Gemini 重试/切号机；完整 P5 再接重试？
4. 真实测试用哪个账号 tier/chat 模型，授权窗口如何控制（每次 1 请求、不重试）？
5. OpenAI 错误体最小字段：是否只做 `403/429/500/502`，是否包含 `error.type/code/param`？
6. 目标 worktree 的 Python 运行方式：`uv run` 还是创建 `.venv`？
7. 真实测试的 Key 环境变量名与 1 个测试账号的准备方式。

## 只读声明

本次审查只读；除新增本报告外，未修改任何任务包、源码或其他文档，未 commit，未访问真实账号/IP/Google。
