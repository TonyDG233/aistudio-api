# REV-B3-01 P6 保活 / 24h 回写 / 429 冷却 review

- 被审包：`docs/dev/rebuild/domains/P6-keepalive/packages/PKG-B3-01-p6-keepalive.md`
- reviewer：只读 agent
- 状态：CONDITIONAL
- 基线：目标 worktree `.kilo/worktrees/rebuild-upstream` HEAD `755e28a`（`99c38ba` + `9b5cdea`）
- 核对方式：只读文件与行号核对；未运行测试，未修改被审包。

## 结论

范围与 D-02/D-03 的边界基本正确，但文件域引用了目标树不存在的路径，且 24h 回写/元数据 24h 所依赖的持久化字段在目标树中不存在；同时把 fork 的 pool/slot/build-only 概念带入了只保留 `active_requests` 的目标架构。以上问题与 B2 `switch_to_account` 未冻结、build-only 真实验收后置 C1 一起，使 B3 的退出条件尚不能闭合，需用户裁决后再批准。

## 发现

| 编号 | 严重度 | 文件:行 | 事实 | 影响 |
|---|---|---|---|---|
| P6-01 | 高 | `PKG-B3-01:25` | 修改文件域写 `gateway/session.py`、`gateway/client.py`、`browser/browser_engine.py`、`browser/cdp_client.py`、`gateway/model_defaults.py`；目标树实际为 `src/aistudio_api/infrastructure/gateway/session.py`、`.../client.py`、`.../model_defaults.py`、`src/aistudio_api/infrastructure/browser/browser_engine.py`、`.../cdp_client.py`，`src/aistudio_api/gateway/` 与 `src/aistudio_api/browser/` 均不存在。 | 文件域不可直接执行；按包实施会新建错误目录或漏改真实文件。 |
| P6-02 | 高 | `PKG-B3-01:12,41,25` | 包要求“失败不提前推进 `auth_saved_at`”“metadata 24h 后再试”；目标 `AccountMeta` 的时间戳只有 `last_used`（`src/aistudio_api/infrastructure/account/account_store.py:98-108`），无 `auth_saved_at`/`tier_checked_at`；§2 也未列 `account_store.py`、`account_rotator.py`、`account_service.py` 等持久化改动。 | 24h 回写与元数据刷新没有可落盘的字段，逐 slot 记账/24h 重试条件无法实现，文件域不闭合。 |
| P6-03 | 高 | `PKG-B3-01:12,25,33` | 包写“build-only、逐 slot 记账”“`client.py`（pool/build-only 回写）”；目标 `AIStudioClient` 只有单 `BrowserSession`（`src/aistudio_api/infrastructure/gateway/client.py:43-274`），无 `InstancePool`/slot/build-only。06 D2 明确“取消 lease/reservation，只保留 `active_requests`”（`06-decision-log.md:50`）。 | 把 fork 的 pool/slot 概念带入目标架构，存在重新引入多实例/pool 的风险，与 D-05/D2 边界冲突；build-only 仅 Fake 验证的退出条件也未定。 |
| P6-04 | 中 | `PKG-B3-01:18,75` | D08 行号/末列与台账不符：D08-014 是 active_activity，末列为 `drop`（`D08-keepalive-rotation.md:19`），不是包所写 `keep`；D08-015 auto_switch 为 `keep`、D08-016/017 为 `port`、D08-018 legacy 为 `drop`（`D08-keepalive-rotation.md:20-23`），而 06 P6-Q4 写“D08-015..018 → drop”。 | §8 的“keep 与 disable 冲突”描述失真；按 D08 ID 审计时无法复核，跨 06/台账的 drop/keep 冲突未被完整披露。 |
| P6-05 | 中 | `PKG-B3-01:25` | keepalive 默认值只写 `config.yaml`/`defaults/config.yaml`/`model_defaults.py`，未列任何 `web/` 现有设置卡片；D-12 要求新设置进入现有 UI，不得只改 YAML。 | keepalive 配置的可见性与 D-12 边界未说明，可能形成“只能改 YAML”的设置。 |
| P6-06 | 中 | `PKG-B3-01:77,80` | build-only 真实验收依赖 C1；手动切号入口是否复用 B2 `switch_to_account` 取决于 B2 是否冻结。两项均只放在 §8。 | B3 的退出条件包含未落地依赖，无法判定 B3 完成。 |
| P6-07 | 低 | `PKG-B3-01:50-57` | 证据列出 `pytest.txt`、`ruff.txt`、`pyright.txt`、`keepalive-fakeclock.json`、`watchdog-posix.txt`、`ws-ping.txt`，但命令只运行 pytest/ruff/pyright，未重定向生成这些文件；命令使用裸 `python`，目标 worktree 无 `.venv`；`watchdog-posix.txt` 在 Windows 本机不可产。 | 证据不能按所写命令复现，审计时需额外约定。 |
| P6-08 | 低 | `PKG-B3-01:27,44` | 目标树不存在 `application/instance_switch.py`，“删除”不适用；`test_browser_engine.py::test_spawn_process_watchdog_creates_pipe_and_reaps` 在目标测试中不存在（现有为 `test_windows_job_object_lifecycle`，`tests/unit/test_browser_engine.py:187`），§4 未标为新增。 | 文件/测试域措辞与目标树不一致，执行时需自行判断。 |

## 验收门槛

- 范围是否闭合：否。build-only 真实回写延后 C1、手动切号待 B2、active_activity 冲突未定。
- 文件域是否可并行：否。5 个源文件路径不存在；缺 `account_store` 持久化文件域；与 B1/B2/B4/P3 的共享文件冲突已标注，但路径需先校正。
- 测试是否可执行：部分。离线 FakeClient/FakeSession 用例可写；POSIX watchdog 用例在 Windows 不能执行；新测试文件均需新建。
- 证据是否可复现：部分。命令未生成所列证据文件，且未指定目标 worktree 的 Python 运行方式。
- 失败停止/回滚是否明确：是。§6/§7 有停止点与回滚点；但回滚点假设存在 B3 提交。
- 是否误用旧 fork 路径或 534 当任务：未把 534 当任务（§1/§2 明确 inventory）；但 §2 的 `gateway/`、`browser/` 路径与 pool/slot/build-only 概念来自 fork，存在旧 fork 路径/概念混入。
- D-02/D-03 边界：§1 与 §2 的 watchdog 仅孤儿回收、只保留 WS ping、删除周期 liveness/Runtime.evaluate 轮询，与 D-02/D-03 一致；P3 冲突已标注。
- D-09/D-12 边界：本包不触碰 snapshot hash，D-09 无冲突；D-12 的“新设置进现有 UI”未在文件域说明，见 P6-05。

## 给用户的裁决项

1. `auth_saved_at`/`tier_checked_at`：是否新增到 `AccountMeta`/registry（会扩大文件域到 `account_store.py` 并涉及数据迁移），还是复用现有 `last_used` 或选择不持久化？
2. pool/slot/build-only：是否按 06 D2 不引入 pool，只保留 1 账号 1 Page + `active_requests`？若不引入，D08-009/010 的 pool 回写如何处置？
3. build-only 回写：是否接受 B3 只用 FakeClient/FakeSession 验证、真实 build-only 验收后置 C1 作为 B3 退出条件？
4. D08-014..018 的 keep/port/drop 与 06 P6-Q4 的 drop 冲突：以哪份为准，是否同步台账？
5. keepalive 配置归属：合并进现有 `rotator` 段，还是新增 `keepalive` 段；是否需要在现有 Settings 卡片暴露（D-12）？
6. 手动切号入口是否必须复用 B2 `switch_to_account`；B2 未冻结时 B3 是否整体阻塞？
7. 证据是否接受由实施者重定向生成 `pytest.txt`/`ruff.txt`/`pyright.txt` 等文件；POSIX watchdog 证据在何环境产出？

## 只读声明

本次审查只读；除新增本报告外，未修改任何任务包、源码或其他文档，未 commit，未访问真实账号/IP/Google。
