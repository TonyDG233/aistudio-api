# REV-2.2.0.7 — `tools/verify_qr_adb.py` 重置工作资料 / GMS 升级 / `DeepLinkActivity` 完成判定 / `help` 速查手册独立只读审查报告

- **审查对象**：`tools/verify_qr_adb.py`（4571 行，`+814 / -20`）与 `tests/unit/test_verify_qr_adb.py`（2324 行，`+610 / -20`，共 116 个单元测试用例）
- **审查日期**：2026-11-15（初审 `CONDITIONAL`；2026-11-15 整改后复审更新为 **`PASS`**）
- **审查结论**：**`PASS`**（整改复审全绿：7 项门禁全绿、116/116 用例通过、隐私零残留；初审全部 `P1-1`、`P2-1`~`P2-3`、`Q-1`~`Q-2` 与 `N-3` 已全部闭环，逐项关闭证据见 §6）
- **环境**：Windows 11 + PowerShell 7，Python `3.13.9`，`uv` 托管虚拟环境
- **100% 只读声明**：本报告由独立只读 Reviewer 出具，**未修改任何业务代码、工具脚本或测试文件**；仅写入授权白名单内的本审查报告与域索引文件（`P10-android-apk/index.md`、`domains/INDEX.md`）；未启动真实服务、未连接真实 ADB/Google 账号、未执行 `git commit` / `git push`。

---

## 1. 审查范围与实机结论核对

| 核查项 | 位置 | 核查结果 |
|---|---|---|
| 顶部实机验证成功结论留档 | `tools/verify_qr_adb.py:4-14` | 已更新为「Island 工作资料（User 10）独立设备 ID 扫码验证已成功打通」：确认此前报 `doesn't meet device security requirements` 根因为 GMS 版本偏旧（`26.43.30`），升至 `26.44.32+` 并在工作资料内开启 VPN + 登录账号后可 100% 通过验证，达上限时可用 `reset-island` 重建获取新 `ANDROID_ID` |
| `DeepLinkActivity` -> `Chrome` 自动结束判定 | `tools/verify_qr_adb.py:96-101, 3039-3044, 3113, 3160-3199` | 正常路径与 `Chrome -> DeepLinkActivity -> Chrome` 正确；**存在 `P1-1` 空焦点过渡帧误触发提前退出缺陷**（见 §2） |
| `update-gms` 升级页唤起 | `tools/verify_qr_adb.py:92-95, 3578-3733` | 优先 `market://`（`_shell_quote` 转义），失败自动回退 `https://play.google.com/...` + `BROWSABLE`；支持 `--user` 与 `--all-users` 互斥校验 |
| `reset-island` 四阶段重建与迁移 | `tools/verify_qr_adb.py:91, 770-803, 3736-3997` | 四阶段主链完整（销毁旧资料 -> 引导新建 -> 校验新旧及主副隔离 -> `setup-island` + 唤起 VPN），`--no-open-vpn` 正确跳过打开 VPN；存在 `P2-1`~`P2-3` 可加固项 |
| `help` 与无参数速查手册 | `tools/verify_qr_adb.py:4005-4135, 4345-4357, 4523-4525` | 无参运行与 `help` 均退出码 `0` 并输出 9 个子命令中文速查表；`help <topic>` 输出速查条目 + argparse 详表，未知子命令返回退出码 `3` |
| 隐私与敏感信息零残留 | `tools/verify_qr_adb.py`、`tests/unit/test_verify_qr_adb.py` 全文 | 原示例 IP `192.168.1.7` 已统一脱敏为 `192.168.1.100`；零真实家庭 IP、零真实手机序列号、零真实 `ANDROID_ID`、零邮箱与本机绝对路径 |

---

## 2. 重点专项核查（含精确 `file:line`）

### 2.1 `DeepLinkActivity` -> `Chrome` 退出完成判定状态机（`tools/verify_qr_adb.py:3039-3199`）

1. **标准序列（`#1~#11` 为 `com.google.android.gms/.recaptchabase.modac.DeepLinkActivity`，`#12` 退回 `com.android.chrome/com.google.android.apps.chrome.Main`）**：
   - `#1~#11`：`focus.package == "com.google.android.gms"`，命中 `GMS_VERIFY_FOCUS_MARKERS`（`:96-101`），`_is_gms_verify_focus(focus)`（`:3039-3044`）返回 `True` -> `on_target = True` -> `gms_verify_active = True`（`:3182`）-> `saw_gms_verify_ui = True`（`:3184`）。
   - `#12`：焦点切回 `com.android.chrome/com.google.android.apps.chrome.Main` -> `gms_focus = False` -> `gms_verify_active = False`，`on_target = True`（`consecutive_off = 0`）。行 `:3192` `if saw_gms_verify_ui and not gms_verify_active:` 立即命中，在 `#12` 当场以 `"GMS 验证界面已完成并关闭"` 置 `completed = True` 并 `break`。
2. **先经 Chrome 再跳转序列（`#1` `Chrome` -> `#2~#10` `DeepLinkActivity` -> `#11` 退回 `Chrome`）**：
   - `#1`：`gms_focus = False`，`saw_gms_verify_ui` 仍为 `False`，`on_target = True`（`consecutive_off = 0`），不会在 `#1` 提前退出。
   - `#2~#10`：`saw_gms_verify_ui = True`。
   - `#11`：退回 `Chrome` 时 `saw_gms_verify_ui and not gms_verify_active` 为真，准确完成退出。
3. **空焦点过渡帧边界（`dumpsys window` 偶发返回空焦点，`focus.component is None`）—— `P1-1` 实证缺陷**：
   - 当 `dumpsys window` 在窗口切换动画或 Activity 过渡瞬间返回空焦点（`mCurrentFocus=null` 且无可用 `mFocusedApp`/`mResumedActivity`）时，`focus.package is None` 且 `focus.component is None`。
   - 此时 `_is_gms_verify_focus(focus)`（`:3041-3043`）因 `haystack == ""` 返回 `False`，导致 `:3182` `gms_verify_active = False`。
   - 行 `:3192` `if saw_gms_verify_ui and not gms_verify_active:` **未校验 `bool(focus.component)`（或 `bool(focus.package)`）**，也没有像 `:3196` `consecutive_off >= 2` 那样做过渡防抖。只要进入过 `DeepLinkActivity`（`saw_gms_verify_ui == True`），任意单次空焦点过渡帧就会立即误触发退出并误报 `"GMS 验证界面已完成并关闭"`。

### 2.2 `reset-island` 与 `update-gms` 的安全性、转义与轮询边界

1. **`AdbClient` 新增方法核查（`tools/verify_qr_adb.py:752-803`）**：
   - `open_view`（`:752-768`）：`url` 经 `self._shell_quote(url)` 单引号转义，无注入风险，超时 `max(self.timeout, 90.0)` 合理。
   - `start_settings_action`（`:770-775`）、`start_component`（`:777-782`）、`resolve_launcher_activity`（`:788-798`）、`launch_monkey`（`:800-802`）：均未调用 `self._shell_quote(...)`（详见 `P2-1`，其中 `RESOLVED_COMPONENT_RE` 允许 `$` 字符，在未加引号的 `adb shell` 中会触发变量展开）。
   - `remove_user`（`:784-786`）：仅在 `old_work.is_work_profile`（`user_id != 0`）时调用，且之后二次调用 `list_users()` 实证是否真被移除（`:3843-3846`），安全性可靠。
2. **`reset-island` 阶段 1~4 轮询、超时与 `--no-open-vpn` 核查（`tools/verify_qr_adb.py:3808-3997`）**：
   - 阶段 1 与阶段 2 共用 `deadline = started + max(timeout, 0.0)`（`:3823`），`clock() >= deadline` 检查位于每次休眠前（`:3881, 3921`），不会死循环；支持工作资料被就地重建（同 `user_id` 但 `ANDROID_ID` 变更）直接进入阶段 3。
   - 阶段 3（`:3935-3952`）强制执行 `assert_space_isolated` 并额外校验 `new_identity.android_id.lower() == old_android_id.lower()` 抛出 `SafetyError`。
   - 阶段 4（`:3967-3981`）：`--no-open-vpn` 将 `args.open_vpn` 置 `False`，仅执行 `run_island_setup` 迁移 VPN 映射而不拉起 VPN 界面；`--no-vpn` 将 `include_vpn` 置 `False`，同时跳过 VPN 映射与拉起。
   - 次要问题：`ResetIslandReport.new_gms_version` 取自阶段 4 之前的 `new_identity`（`:3937, 3992`），见 `P2-3`；`monkey` 回退无 `--user` 参数，见 `P2-2`。

---

## 3. 全量问题清单（P0 / P1 / P2 / Q / N）

### P0（阻断性缺陷）
- **无。**

### P1（高优先级缺陷，需修复后转 `PASS`）

- **`P1-1` `DeepLinkActivity` 监控期间遇到空焦点过渡帧（`focus.component is None`）会立即误触发提前退出。**
  - 位置：`tools/verify_qr_adb.py:3039-3044`（`_is_gms_verify_focus`）、`:3160`、`:3182-3195`（`gms_verify_active` 与 `if saw_gms_verify_ui and not gms_verify_active:`）。
  - 原因：当 `dumpsys window` 在过渡瞬间返回空焦点（`focus.component is None` 且 `focus.package is None`）时，`_is_gms_verify_focus` 返回 `False`、`gms_verify_active` 为 `False`，而 `:3192` 未校验当前确实已切换到非空的真实前台窗口（`bool(focus.component)` 或 `bool(focus.package)`），导致单次空采样即中断监控并误报 `"GMS 验证界面已完成并关闭"`。
  - 修复建议：`:3192` 改为仅在**存在明确非空前台焦点且不再是 GMS 验证界面**（`if saw_gms_verify_ui and bool(focus.component) and not gms_verify_active:`，或要求连续 2 帧空焦点才退出）时才判定 GMS 验证完成关闭；注意在 `WindowFocus` 上不要用 `bool(focus)`（dataclass 实例恒为 `True`），必须判断 `focus.component` 或 `focus.package`。

### P2（中优先级缺陷，建议本轮一并收口）

- **`P2-1` `AdbClient` 新增命令组装未统一使用 `_shell_quote`，含 `$` 的 Java 内部类 Activity 组件名存在 shell 变量展开风险。**
  - 位置：`tools/verify_qr_adb.py:770-802`（`start_settings_action`、`start_component`、`resolve_launcher_activity`、`launch_monkey`）与 `:865`（`RESOLVED_COMPONENT_RE` 允许 `$`）。
  - 影响：若某 VPN 应用的启动 Activity 为内部类（如 `pkg/.Main$Launcher`），未加单引号的 `am start --user 10 -n pkg/.Main$Launcher` 在设备端 `mksh` 中会把 `$Launcher` 展开为空串导致拉起失败。
  - 建议：对上述 4 个方法中的 `action`、`component`、`package` 统一使用 `self._shell_quote(...)`。

- **`P2-2` `_launch_vpn_in_work_profile` 的 `monkey` 回退实际在主空间（User 0）而非工作资料（User 10）启动应用。**
  - 位置：`tools/verify_qr_adb.py:800-802`（`launch_monkey`）、`:3793-3805`（`_launch_vpn_in_work_profile`）、`:3972-3976`（提示文案）、`:1058`（`parse_resolved_component` 跳过含空格的 `u10 pkg/act` 行）。
  - 影响：Android 原生 `monkey` 不支持 `--user`，回退走 `monkey` 时实际拉起的是 User 0 的应用，但输出提示仍写「已在手机壶中界为您自动打开梯子软件」。
  - 建议：若 `resolve_launcher_activity` 返回 `u10 pkg/act` 应允许剥离 `u<uid>` 前缀后再解析；若仍需回退 `monkey`，提示中应如实注明“已尝试通过 monkey 唤起（可能落在主空间）”。

- **`P2-3` `ResetIslandReport.new_gms_version` 在 `setup-island` 映射 GMS 之前采集，可能漏报新工作资料的 GMS 版本。**
  - 位置：`tools/verify_qr_adb.py:3937, 3992`。
  - 影响：全新创建的工作资料在 `run_island_setup` 执行 `pm install-existing` 前可能尚未映射 GMS，导致报告中的 `新空间 GMS 版本` 显示为 `(未知)`。
  - 建议：在阶段 4 `run_island_setup` 完成后，若 `new_identity.gms_version` 为空（或直接重新调用一次 `adb.gms_version(new_work.user_id)`），再写入 `ResetIslandReport.new_gms_version`。

### Q（验证覆盖缺口）

- **`Q-1`**：`tests/unit/test_verify_qr_adb.py` 缺少两条核心状态机回归用例：(1) `Chrome -> DeepLinkActivity -> Chrome`（验证先经过浏览器不提前退出，回到浏览器才结束）；(2) `DeepLinkActivity -> 空焦点过渡帧 (mFocusedApp=null) -> DeepLinkActivity -> Chrome`（验证 `P1-1` 修复后过渡帧不误触发提前退出）。
- **`Q-2`**：缺少 `reset-island --no-vpn`（不映射也不打开 VPN）及 `run_update_gms` 双通道均失败返回 `report.ok is False` / CLI 退出码 `1` 的单元测试。

### N（现状备注与通过项）

- **`N-1`（隐私零残留）**：全文及全量 diff 扫描确认无真实家庭 IP、序列号、`ANDROID_ID`、邮箱或本机绝对路径；原示例 IP `192.168.1.7` 已替换为 `192.168.1.100`。
- **`N-2`（CLI `help` 体验）**：`COMMAND_GUIDE` 9 个子命令与 `build_parser()` 注册的 9 个子命令一一对应，无参数运行、`help`、`help <子命令>` 输出清晰完整；`help unknown` 正确返回退出码 `3`。
- **`N-3`（工具边界提示）**：`run_reset_island` 的 `--interval` 未做 `> 0` 校验（若外部传入 `<= 0` 且使用非推进的假时钟会死循环），实机场景下 `time.monotonic()` 推进不受影响，可在后续顺手补 `max(interval, 0.1)` 保护。

---

## 4. 必跑门禁与实操命令实测结果

| # | 命令 | 实测输出摘要 | 退出码 | 判定 |
|---|---|---|---|---|
| 1 | `uv run python tools/verify_qr_adb.py`（无参） | 输出全部 9 个子命令中文速查手册（含 `reset-island`、`update-gms`） | `0` | **PASS** |
| 2 | `uv run python tools/verify_qr_adb.py help` | 输出全部 9 个子命令中文速查手册 | `0` | **PASS** |
| 3 | `uv run python tools/verify_qr_adb.py help reset-island` | 输出 `reset-island` 中文详情 + argparse 全部参数表 | `0` | **PASS** |
| 4 | `uv run --no-sync ruff check tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py` | `All checks passed!` | `0` | **PASS** |
| 5 | `uv run --no-sync ruff format --check tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py` | `2 files already formatted` | `0` | **PASS** |
| 6 | `uv run --no-sync pyright`（及指定双文件） | `0 errors, 0 warnings, 0 informations` | `0` | **PASS** |
| 7 | `uv run pytest tests/unit/test_verify_qr_adb.py -q` | `110 passed in 1.14s` | `0` | **PASS** |
| 8 | `git diff --check` | 无空白与行尾错误 | `0` | **PASS** |

> 所有命令均在 `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'` 下实跑。

---

## 5. 整改闭环要求（初审记录）

请 Worker 仅在白名单内修复 **`P1-1`**（建议顺带收口 `P2-1`~`P2-3`）并补齐 **`Q-1`** / **`Q-2`** 回归用例，随后再交由独立 Reviewer 复审转 `PASS`。

---

## 6. 整改复审与闭环结论（2026-11-15）

独立只读 Reviewer 已逐行核对整改后 `tools/verify_qr_adb.py` 与 `tests/unit/test_verify_qr_adb.py` 的全部改动，并独立实跑全部门禁命令。初审提出的全部 `P1`、`P2`、`Q` 及 `N-3` 均已关闭，无新引入问题。

### 6.1 逐项关闭证据

| 编号 | 整改实现与精确位置 | 回归测试证据 | 状态 |
|---|---|---|---|
| `P1-1` | `run_verification`（`tools/verify_qr_adb.py:3189, 3222`）新增 `has_focus = bool(focus.component or focus.package)`，退出判定收紧为 `if saw_gms_verify_ui and has_focus and not gms_verify_active:`，空焦点过渡帧不再触发误退出 | `test_run_verification_ignores_empty_focus_transition_frame`（`tests/unit/test_verify_qr_adb.py:1811-1836`，序列 `[DeepLinkActivity, 空焦点(mCurrentFocus=null), DeepLinkActivity, Chrome]` 断言采集满 4 样本、第 2 帧 `focus.component is None` 且在回到 Chrome 后正常完成） | **CLOSED** |
| `P2-1` | `AdbClient.start_settings_action`（`:770-775`）、`start_component`（`:777-782`）、`resolve_launcher_activity`（`:788-798`）及新增 `start_package_launcher`（`:800-807`）全部统一调用 `self._shell_quote(...)` | `test_shell_quote_protects_component_action_and_package`（`tests/unit/test_verify_qr_adb.py:1097-1116`，覆盖含 `$` 内部类组件名与特殊包名/Action，逐条断言单引号包裹命令串） | **CLOSED** |
| `P2-2` | 移除全局 `launch_monkey`；`_launch_vpn_in_work_profile`（`tools/verify_qr_adb.py:3823-3836`）改用 `adb.start_package_launcher(vpn_pkg, user_id=user_id)`（`am start --user <uid> -a MAIN -c LAUNCHER -p '...'`）在工作资料内定向拉起；同步升级 `parse_resolved_component`（`:1059-1092`）按空白分词提取组件并剥离 `u<uid>` 前缀、过滤纯数字与 `1/2` 误匹配 | `test_parse_resolved_component`（`tests/unit/test_verify_qr_adb.py:450-470`）与 `test_run_reset_island_non_root_fallback_waits_and_opens_settings`（`:2174-2202`，断言 `vpn_open_method == "am start -p (com.v2ray.ang)"`、存在 `--user 10 ... -p 'com.v2ray.ang'` 且无任何 `monkey` 调用） | **CLOSED** |
| `P2-3` | `run_reset_island`（`tools/verify_qr_adb.py:3995, 4024`）在阶段 4 `run_island_setup` 映射/启用完成后再调用 `adb.gms_version(new_work.user_id)` 并填入 `ResetIslandReport.new_gms_version` | `test_run_reset_island_direct_remove_migrates_and_opens_vpn`（`tests/unit/test_verify_qr_adb.py:2144-2171`，开启 `gms_mapped_after_setup_only=True` 模拟先未映射、setup 后才映射的场景，断言 `report.new_gms_version == "26.44.32"`） | **CLOSED** |
| `Q-1` | 补齐先经 Chrome 再进 `DeepLinkActivity` 后退回 Chrome、以及空焦点过渡帧防抖两条回归用例 | `test_run_verification_completes_chrome_to_deeplink_to_chrome`（`:1787-1809`）与 `test_run_verification_ignores_empty_focus_transition_frame`（`:1811-1836`） | **CLOSED** |
| `Q-2` | 补齐 `reset-island --no-vpn` CLI 与 `update-gms` 双通道均失败返回码 `1` 的回归用例 | `test_cli_reset_island_no_vpn_skips_vpn_mapping_and_launch`（`:2226-2248`）与 `test_run_update_gms_fails_when_both_market_and_https_fail`（`:2012-2031`） | **CLOSED** |
| `N-3` | `run_reset_island`（`tools/verify_qr_adb.py:3852`）对轮询间隔执行 `interval = max(0.1, float(interval))` 下限钳位 | `test_run_reset_island_clamps_interval_minimum`（`tests/unit/test_verify_qr_adb.py:2251-2274`，传入 `interval=0.0` 验证所有 `sleeper` 调用均 `>= 0.1`） | **CLOSED** |

### 6.2 复审门禁实跑结果（2026-11-15）

| # | 命令 | 实测输出摘要 | 退出码 | 判定 |
|---|---|---|---|---|
| 1 | `uv run python tools/verify_qr_adb.py`（无参） | 输出全部 9 个子命令中文速查手册 | `0` | **PASS** |
| 2 | `uv run python tools/verify_qr_adb.py help` / `help reset-island` | 输出总手册 / `reset-island` 详情与参数详表 | `0` | **PASS** |
| 3 | `uv run --no-sync ruff check tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py` | `All checks passed!` | `0` | **PASS** |
| 4 | `uv run --no-sync ruff format --check tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py` | `2 files already formatted` | `0` | **PASS** |
| 5 | `uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations` | `0` | **PASS** |
| 6 | `uv run pytest tests/unit/test_verify_qr_adb.py -q` | `116 passed in 0.47s` | `0` | **PASS** |
| 7 | `git diff --check` | 无空白与行尾错误 | `0` | **PASS** |

- **最终复审结论：`PASS`**（`P0` 无，`P1-1`、`P2-1`~`P2-3`、`Q-1`~`Q-2`、`N-3` 全部关闭，7 项门禁全绿，隐私扫描零残留）。
