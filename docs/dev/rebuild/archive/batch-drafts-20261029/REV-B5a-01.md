# REV-B5a-01 toolcall review

- 被审包：`docs/dev/rebuild/domains/P5-generation/packages/PKG-B5a-01-toolcall.md`
- reviewer：只读 agent
- 状态：CONDITIONAL
- 基线：目标 worktree `.kilo/worktrees/rebuild-upstream` HEAD `755e28a`（`99c38ba` + `9b5cdea`）
- 核对方式：只读文件、fixture 字节与行号核对；未运行测试，未修改被审包。

## 结论

包的基线声明正确：534 只是 inventory，09 仅作排查参考，文件域与目标树基本对齐。但 PG-03 的 `f03_finish_only.bin` 实际不含 finish，所写“先读 finish 再处理空 candidates”的 `wire_parser` 修复无法成立，导致 PG-01..PG-04 门槛中的 PG-03 不能按当前文本闭合。PG-04 的字符串 args 策略、PG-08 的归属与 PG-03 有界窗口秒数也仍未定，因此本包应判 CONDITIONAL，且在 PG-03 更正前不得批准。

## 发现

| 编号 | 严重度 | 文件:行 | 事实 | 影响 |
|---|---|---|---|---|
| B5a-01 | 阻断 PG-03 退出 | `PKG-B5a-01:28,46`；`tests/fixtures/pg/f03_finish_only.bin`（22 字节） | `f03_finish_only.bin` 实际内容为 `)]}'` + `[[],null,[5,3,8]]`（22 字节，无 finish 字段）；WIRE_SPEC 明确 `candidate[1]` 才是 FinishReason（`docs/WIRE_SPECIFICATION.md:260-263`）。candidates 为空时不存在 candidate，因而不存在可“丢失”的 finish；`parse_response_chunk` 的提前 return 在 `wire_parser.py:447-448`，finish 读取在 `:523-528`，把 return 移到 finish 读取之后不会改变空 candidates 的结果。 | §4 的“finish-only chunk 不丢 finish”无法用该 fixture 复现/断言；§2 的 PG-03 `wire_parser` 修复是空操作。PG-03 门槛不能按当前文本关闭。 |
| B5a-02 | 中 | `PKG-B5a-01:47,80` | PG-04 写“字符串 args 先试 JSON，失败按约定包装或明确 400”；§8 又把“包装还是严格 400”列为待裁决。 | PG-04 是必须复现/修复/回归的门槛项，但失败分支的期望行为未定，测试无法唯一断言。 |
| B5a-03 | 中 | `PKG-B5a-01:18`；`PKG-B5b-01:18` | 包把 PG-08（OpenAI/Claude 严格工具历史）defer 到 B5b；但 B5b 明确“tools/vision/reasoning 全量 parity”在范围外，且只做无工具的 OpenAI Chat 最小闭环，B5b 文件域/测试中没有工具历史项。 | PG-08 在 B5b 内没有承接点，实际处于无包承接状态。 |
| B5a-04 | 中 | `PKG-B5a-01:37,56-58` | 包写“`.venv\Scripts\python.exe` 可用”并要求在目标 worktree 用它执行；目标 worktree `.kilo/worktrees/rebuild-upstream` 无 `.venv`（主工作区有 `.venv`），README 的测试命令为 `uv run pytest`（`README.md:411`）。 | §5 命令在目标 worktree 直接执行会失败，测试/证据不可按包复现。 |
| B5a-05 | 中 | `PKG-B5a-01:29,79` | PG-03 要求“完整工具调用无 finish 时按有界窗口合成终块”，但“短有界窗口”秒数未定，§8 仅提问；同时明确不得用默认 600s。 | PG-03 的测试与退出条件依赖该值，当前无法写出确定断言。 |
| B5a-06 | 低 | `PKG-B5a-01:27` | 包写“修改 `wire_types.py`：占位签名写下标 14”；目标 `wire_types.py:274-275` 已在 `thought_signature` 非空时写下标 14，占位签名注入点在 `chat_service.py:588-598`（缺失签名时不注入）。 | “写下标 14”不是需要修改的行为，可能与 PG-01 的实际改动点混淆。 |
| B5a-07 | 低 | `PKG-B5a-01:30` | 包列 `routes_gemini.py` 为 PG-02 修改文件；目标 `routes_gemini.py:29,39` 仅委托 `handle_gemini_generate_content`，流式路径在 `api_service_gemini.py`。 | 文件域可能包含无需改动的文件，增加冲突面。 |
| B5a-08 | 低 | `PKG-B5a-01:56-60` | 证据要求保存 JUnit XML、失败/通过日志、`[toolshape]` 摘要、triage 表；命令只对第二次 pytest 指定 `--junitxml`，首次 pytest 与 ruff/pyright 未重定向，`pytest.txt`/triage 表未生成。 | 证据不完整，审计需额外补命令。 |

## 验收门槛

- 范围是否闭合：否。PG-03 的 fixture/修复不成立，PG-04 失败分支未定，PG-03 窗口秒数未定。
- 文件域是否可并行：基本可并行。B5a 已标注 `chat_service.py`/`api_service_gemini.py` 与 B5b 的只读/冲突关系；`routes_gemini.py` 可能无需修改。
- 测试是否可执行：部分。离线 harness 可写；目标 worktree 无 `.venv`，PG-03 断言不可唯一确定。
- 证据是否可复现：部分。JUnit 可生成；`pytest.txt`、triage 表、`[toolshape]` 摘要未由命令生成。
- 失败停止/回滚是否明确：是。§6 有真实网络/两轮不绿/越界改动停止点；§7 有按文件回退与 fixture 字节校验。
- 是否误用旧 fork 路径或 534 当任务：未把 534 当任务；file:line 已重锚到目标树（`wire_parser.py:446-448,523-528` 与目标一致）。但 PG-03 的“finish 落在空 candidates”前提来自 09 旧结论，未按目标树/WIRE_SPEC 复验，属于旧 09 结论的残留。

## 给用户的裁决项

1. PG-03 fixture：是否允许重建 `f03_finish_only.bin` 为带 finish 的 chunk，或删除“finish-only 丢 finish”断言、只保留“有完整 functionCall 但无 finish 的有界窗口”用例？
2. PG-03 有界窗口秒数：取多少（必须小于客户端超时，且不得默认 600s）？
3. PG-04 字符串 args 失败分支：包装为约定结构，还是返回明确 400？
4. PG-08 归属：B5b 明确不做 tools，是否改 defer 到 C3 或后续 P5，而不是 B5b？
5. PG-05/06/07/11/12/13 的 triage 结果：接受当前 `defer` 预期，还是其中某项升级为 `block`；哪些阻塞 B5b？
6. `[toolshape]` 日志级别与采样策略。
7. 目标 worktree 的 Python 运行方式：使用 `uv run`，还是在 worktree 内创建 `.venv`？

## 只读声明

本次审查只读；除新增本报告外，未修改任何任务包、源码或其他文档，未 commit，未访问真实账号/IP/Google。
