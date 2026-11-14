# REV-2.2.0.3 — `tools/verify_qr_adb.py` 二维码 + ADB + Island 全链路工具独立只读审查报告

> **【实机验证结论留档：Island 工作资料绕过设备验证尝试已失败】**
>
> 经真机控制变量实测，通过 Android `FLAG_MANAGED_PROFILE`（Island / 壶中界 `User 10`）隔离 `ANDROID_ID` 与独立 KeyMint 密钥以突破 Google AI Studio `verify-account`（`recaptcha.net/qr/`）单设备验证限额的方案**已证实无效**：Google 的 `recaptcha.net/qr/`（DroidGuard / Play Integrity）会检测并直接拒绝第三方受管工作资料（Managed Profile）沙盒环境（返回 `We couldn't verify your account with this device... or doesn't meet device security requirements`），而切回同一台物理手机的原生主空间（`User 0`）则可正常通过验证。
>
> 因此本脚本无法用于重置或绕过单台物理手机的验证配额限制，仅保留作为剪贴板二维码离线解码与主空间（`--allow-main-user`）免摄像头 ADB 辅助推送工具留底。本报告 §1~§7 的 `PASS` 仅代表**离线代码质量与门禁**结论，不代表该绕过方案在实机可用。

- **审查对象**：`tools/verify_qr_adb.py`（2969 行，新增未跟踪文件）与其离线单元测试 `tests/unit/test_verify_qr_adb.py`（1051 行，59 用例，新增未跟踪文件）；附带核验 `data/tools/island/Island.v6.2.1.apk` 的存在性/体积/SHA-256/git 跟踪状态。
- **审查日期**：2026-11-14（初版 `CONDITIONAL`）；2026-11-14 整改后复审更新为 `PASS`
- **审查结论**：**`PASS`**（整改后复审：五项门禁全绿、59/59 用例通过；`P1-1`~`P1-3`、`P2-1`~`P2-8`、`Q-1`~`Q-4` 全部闭环，逐项证据见 §7）
- **环境**：Windows 11 + PowerShell 7，Python `3.13.9`，`uv` 托管虚拟环境；本机可用二维码后端实测为 `['cv2', 'zxingcpp', 'builtin']`。
- **只读声明**：初版审查由独立只读 Reviewer 执行；整改由收口 Worker 在授权白名单内完成（仅 `tools/verify_qr_adb.py`、`tests/unit/test_verify_qr_adb.py` 与本报告、P10 `index.md`），未执行 `git commit` / `git push`，未启动真实服务、未拉起真实浏览器/adb 实机、未连接真实 Google 账号。

---

## 1. 审查范围与产物核验

### 1.1 只读核查文件

| 文件 | 关注点 |
|---|---|
| `tools/verify_qr_adb.py` | 命令注入防护（`AdbClient.resolve_activity` / `start_view` 的 shell 转义）、主副空间防串味（`assert_space_isolated` + 运行期回跳）、内置零依赖 PNG/BMP/DIB 解码 + QR 定位/采样/RS 纠错、verify 状态机 |
| `tests/unit/test_verify_qr_adb.py` | 59 个用例的覆盖边界与测试缝（`runner` / `clock` / `sleeper` / `writer` / `confirm_reader` 注入） |
| `data/tools/island/Island.v6.2.1.apk` | 存在性、体积、SHA-256、APK 魔数、是否被 git 跟踪 |

### 1.2 Island APK 产物核验

| 检查项 | 实测结果 | 判定 |
|---|---|---|
| 存在性 | `data/tools/island/Island.v6.2.1.apk` 存在 | 通过 |
| 体积 | `3,499,707` 字节（约 3.34 MiB） | 通过 |
| SHA-256 | `374e5d23c6e64b71235ad7c9bd7060b7119b71e3bd47b9b216d87c222170f611` | 通过 |
| APK/ZIP 魔数 | 前 4 字节 `50 4B 03 04`（`PK\x03\x04`），与 `APK_MAGIC`（`tools/verify_qr_adb.py:61`）一致 | 通过 |
| 是否被 git 跟踪 | `git ls-files --error-unmatch` 报 `did not match any file(s) known to git`（未跟踪）；`git status --porcelain` 不显示该项，说明被忽略 | 通过 |
| 忽略规则来源 | `git check-ignore -v` → `.gitignore:12:/data/` 精确命中 | 通过 |
| 目录内唯一 APK | `Get-ChildItem data/tools/island` 仅有 `Island.v6.2.1.apk`，`find_latest_island_apk` 取到该文件 | 通过 |

> 结论：APK 体积大且不入库，符合 `.gitignore` 的 `/data/` 约定；`_apk_summary`（`:2144-2151`）的魔数 + SHA-256 校验与实测一致。

---

## 2. 必跑验收门禁实跑结果

| # | 命令 | 实跑输出 | 判定 |
|---|---|---|---|
| 1 | `uv run ruff check tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py` | `All checks passed!` | **PASS** |
| 2 | `uv run ruff format --check tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py` | `2 files already formatted` | **PASS** |
| 3 | `uv run pyright tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py` | `0 errors, 0 warnings, 0 informations` | **PASS** |
| 4 | `uv run pytest tests/unit/test_verify_qr_adb.py` | `59 passed in 0.49s` | **PASS** |
| 5 | `git add -N tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py && git diff --check` | 退出码 `0`；仅一条 CRLF 换行提示（`CRLF will be replaced by LF`），无空白错误（intent-to-add 后两个新增文件已纳入检查） | **PASS** |

> 全部命令均带 `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'`。
> **`N-5` 已关闭**：整改后先执行 `git add -N`（intent-to-add），再跑 `git diff --check`，两个目标文件已实际纳入空白检查，不再等效空跑。

---

## 3. 关键专项核验

> 本节及附录 A 的探针输出为**整改前基线**，用于证明缺陷真实存在；整改后的实现与关闭证据见 §7。

### 3.1 命令注入防护（`&` 截断风险）——**当前无可达注入，但属隐式耦合**

**用户核心疑问：向 `adb shell` 传递含 `&` 的二维码 URL 会不会被 Android shell 截断？答案是：不会。**

证据链（探针实测 ARGV 原文，见附录 A 第 3 段）：

```
('adb', 'shell', "am start --user 10 -W -a android.intent.action.VIEW -c android.intent.category.BROWSABLE -d 'https://x.test/v?a=1&b=2&c=3'")
('adb', 'shell', "cmd package resolve-activity --brief -a android.intent.action.VIEW -c android.intent.category.BROWSABLE -d 'https://x.test/v?a=1&b=2&c=3' --user 10")
```

- **本地侧无 shell**：`default_runner`（`:243-266`）以 `subprocess.run(list(args), ...)` 列表形式执行，未使用 `shell=True`，Windows 下不存在 cmd/PowerShell 二次解析（`N-4`）。
- **设备侧单引号保护**：整条 shell 命令作为**单个 argv** 交给 `adb shell`（`AdbClient.shell`，`:380-382`），设备端 mksh 按单引号把 `-d '...'` 整体视为一个字面量，`&`、`?`、`=`、`%`、`;`、反引号在单引号内均不具语法意义。
- **单引号逃逸实际被上游拦住**：`extract_url`（`:2174-2182`）的提取正则 `r"https?://[^\s\"'<>]+"` 显式排除 `'`。探针实测 `https://x.test/?a=1'; id; '` 被截断为 `https://x.test/?a=1`（附录 A 第 4 段），命令拼接后不可能闭合单引号，故当前**不存在可达的 RCE 路径**。

**但仍属 `P2-1`**：`resolve_activity`（`:463-469`）与 `start_view`（`:471-477`）自身**没有任何转义/校验**，安全性完全依赖"调用方一定先过 `extract_url`"这一隐式约定。任何新增调用点（或把 `AdbClient` 当库复用的脚本）直接传入原始 URL 即形成设备端任意命令执行。建议补一个显式 `_shell_single_quote(url)`（把 `'` 转成 `'\''`）并在两个方法内部兜底，同时补 `&`/`%`/`;`/`+` 的回归用例（`Q-1`）。

### 3.2 主副空间防串味（`assert_space_isolated` + 运行期回跳）

| 检查点 | 代码位置 | 结论 |
|---|---|---|
| 目标空间 ANDROID_ID 为空/`null`/`unknown` 立即中止 | `:762-768` | 严密 |
| 目标空间 ANDROID_ID 与主空间相同立即中止 | `:769-773` | 严密 |
| 默认不落 User 0（未传 `--user` 时只选工作资料） | `:753-759` | 严密 |
| 显式 `--user 0` 二次确认 | `:748-752` | 严密 |
| 注入前先做隔离校验，失败不 `am start` | `:2445-2448`；用例 `test_run_verification_blocks_same_android_id_without_launching` | 严密（用例断言无 `am start`） |
| 运行期 ANDROID_ID 回跳到主空间立即中止 | `:2516-2520`；用例 `test_run_verification_aborts_on_android_id_rollback` | 严密 |
| **主空间 ANDROID_ID 自身为空时隔离校验被"空过"** | `:762-773` 只校验 `target`，未校验 `main.android_id_valid` | **`P2-2` 不严密（fail-open）** |
| **前台窗口缺 `u<uid>` 时被当作目标空间焦点** | `:2525-2529`（`focus_user is None` 直接放行） | **`P1-3` 不严密（fail-open，已实证）** |
| 运行期漂移到**第三方** uid 仅打印警告，报告仍按初始快照判 PASS | `:2521` 与 `:2356-2360` | **`P2-6`** |
| GSF ID 仅展示不参与隔离判定 | `:805-821`、`:2382-2389` | `N-1`（ANDROID_ID 为逐用户主身份，设计可接受） |

`P1-3` 实证（附录 A 第 6 段）：构造 `mCurrentFocus=Window{1b2c com.android.chrome/...}`（**无 `u<uid>` 前缀**，模拟部分 ROM 的 `dumpsys window` 输出），`parse_window_focus` 回填 `user_id=None`，`run_verification` 的 `on_target` 判定为 `True`，最终 `report.completed=True`、`completion_reason='验证界面已从前台关闭'`、`focus_trail` 含该 Chrome 组件。即：**在无法把窗口归属到具体用户时，工具把"未知"当成"目标空间"，可能产出一份无法证明工作资料真的接管了链接的 PASS 报告**。这与本工具"取证"的立身之本直接冲突，必须 fail-closed。

### 3.3 离线二维码解码（PNG/BMP/DIB + 剪贴板）

| 能力 | 代码位置 | 实测 |
|---|---|---|
| 纯 Python PNG 解码（1/2/4/8/16bit、灰度/彩色/调色板/Alpha、非隔行） | `decode_png:845-902`、`_png_unfilter:905-927`、`_png_rows_to_gray:942-960` | **灰度（color_type 0）通过**；**RGBA(6) / LA(4) 失败（`P1-1`）** |
| BMP 8/24/32bpp BI_RGB / BI_BITFIELDS 与裸 DIB | `decode_bmp:1026-1072` | 24bpp 与 32bpp BITFIELDS 实测均可解出（`N-3` 潜在偏移脆弱点） |
| 剪贴板裸 DIB 补 BMP 文件头 | `normalize_image_bytes:1172-1190` | 用例 `test_normalize_image_bytes_wraps_raw_clipboard_dib` 通过 |
| 定位图形 1:1:3:1:1 + 纵横交叉复核 + 聚类 | `:1324-1454` | 通过（含 180° 旋转用例） |
| 多阈值 + 反色重试 | `decode_gray_image:1264-1275` | 通过 |
| RS 分块表 + BM/Chien/Forney 纠错 | `:1745-1995` | 通过（10 处 4×4 块破坏仍可解） |
| 分段解码（numeric/alnum/byte/ECI/Kanji） | `:2042-2136` | 基本覆盖；**mode 9（FNC1 第二位置）漏读 8bit 应用指示符（`P2-5`）** |
| 多后端串联 + 内置兜底 | `decode_qr_from_image:1146-1169` | 通过；本机 `cv2` 排在首位，会**掩盖**内置解码器的 RGBA 缺陷（`N-2`） |

`P1-1` 实证（附录 A 第 1 段）：把已知可解的灰度二维码重打包为同内容的 `color_type=6 (RGBA)` 与 `color_type=4 (LA)` PNG 后，`load_gray_image` 还原的像素与原始灰度图**不一致**（`gray_match=False`），`decode_builtin` 均报 `仅找到 0 个定位图形，需要 3 个`。根因在 `_pack_gray_row`（`:963-989`）：`color_type == 4` 走 `row[:width]`，把交错的 `G,A,G,A...` 当成纯灰度；`color_type == 6` 落入 RGB 分支的 `off = x * 3`，而 RGBA 每像素 4 字节，导致通道错位。16bit 的 LA/RGBA 经 `row[0::2]` 后同样错位。这与模块 docstring"PNG/BMP/DIB 解码（零 DLL 依赖）"的承诺不符，且现有 45 个用例的 fixture 全是 `color_type=0`，完全未覆盖该路径（`Q-2`）。

---

## 4. 测试覆盖评估（整改后 59 用例）

| 覆盖面 | 用例 | 评价 |
|---|---|---|
| `pm list users` / ANDROID_ID / GSF ID / `ps -A` / `dumpsys window` / resolve-activity / devices 解析 | `tests:309-412` | 充分，含空输出、占位值与非法 ANDROID_ID（`P2-7`） |
| 隔离校验与目标空间选择（含 `--allow-main-user`、非工作资料用户拒绝） | `tests:415-508` | 充分（`Q-3` 已补） |
| 内置二维码解码（fixture、破坏纠错、旋转、BMP、DIB 包装、非 QR 拒绝、RGBA/LA 8/16bit、透明合成、解码炸弹） | `tests:510-590` | 充分（`Q-2` 已补） |
| 输入源（URL/图片/剪贴板文本/剪贴板图片） | `tests:593-660` | 充分（剪贴板用 monkeypatch 注入，离线安全） |
| adb 路径（含 `ANDROID_SDK_ROOT`）/ shell 转义 / `local.properties` / APK 摘要 | `tests:692-774` | 充分（`P2-8`/`Q-1` 已补） |
| verify 状态机（正常完成、主空间基线、非工作用户拒绝、同 ID 阻断、无工作资料、ID 回跳/漂移、回车确认、超时、am start 报错、无 `u<uid>` 焦点 fail-closed 与进程兜底、跨用户跳焦拦截） | `tests:776-974` | 充分（`Q-4`/`P2-6` 已补） |
| CLI（help / devices / inspect-ids / install-island） | `tests:976-1051` | 充分 |
| **URL 含 `&` / `%` / `;` / `+` 时的实际 shell 命令串断言** | `test_shell_quote_protects_special_urls` | **`Q-1` 已闭环** |

---

## 5. 全量问题清单（P0 / P1 / P2 / Q / N）

> 本节保留**整改前**的原始问题描述与定位，作为审计追溯；每项的关闭证据与整改后新行号统一见 §7。整改后全部 `P1`/`P2`/`Q` 均已关闭。

### P0（阻断性缺陷）
- **无。**

### P1（高优先级缺陷，实机验收前必须修复）

- **`P1-1` 内置 PNG 解码器对 RGBA(6) / LA(4) 产生错位灰度，零依赖路径下二维码解不出。**
  - 位置：`tools/verify_qr_adb.py:963-989`（`_pack_gray_row`；LA 分支 `:983-985` 用 `row[:width]`，RGBA 落入 `:986-989` 的 `off = x * 3`），16bit 同经由 `:951-953` 的 `row[0::2]`。
  - 实证：附录 A 第 1 段，`color_type=6/4` 均 `gray_match=False` 且 `decode_builtin` 失败（`仅找到 0 个定位图形`）。
  - 影响：显式 `--backend builtin` 或未安装 `cv2`/`zxingcpp` 的干净环境（即工具宣称的"零 DLL 依赖"场景）下，来自 Chrome/系统截图（常见 RGBA PNG）的二维码无法解码。
  - 建议：按 `PNG_CHANNELS[color_type]` 计算步长——LA 取 `row[0::2]`，RGBA 用 `off = x * 4`，16bit 先按 2 字节步长抽通道；补 RGBA/LA（8bit 与 16bit）fixture 用例。

- **`P1-2` `--allow-main-user` 选项永远无法通过，属声明了却不可用的死路。**
  - 位置：`tools/verify_qr_adb.py:2785-2789`（CLI 帮助"允许目标空间为主空间 User 0"）、`:748-752`（放行 User 0）、`:2445-2448`（随后无条件调用隔离校验）、`:762-773`（同一用户必然 `same_android_id` → 抛错）。
  - 实证：附录 A 第 2 段，`--allow-main-user` 选中 `User 0` 后 `assert_space_isolated` 必然抛"完全相同，Island 隔离未生效"。
  - 影响：操作者若要采集主空间基线（对照用），该选项 100% 失败，且错误信息误导为"隔离未生效"而非"主空间模式不支持隔离校验"。
  - 建议：二选一——(a) 删除该 flag；(b) 主空间模式显式跳过 `assert_space_isolated`，报告标注"主空间基线模式：无隔离证明"，并把 `isolation_ok` 置为不适用。

- **`P1-3` 前台窗口缺 `u<uid>` 前缀时按"目标空间"放行，可能产出无法归属的假 PASS。**
  - 位置：`tools/verify_qr_adb.py:644-658`（`_extract_window_component` 无 `u<uid>` 时返回 `None` user）、`:613-641`（`parse_window_focus`）、`:2525-2529`（`focus_user is None or focus_user == target_user.user_id` 直接 `on_target=True`）、`:2530-2540`（据此判定"验证界面已从前台关闭"）。
  - 实证：附录 A 第 6 段，无 `u<uid>` 的 `com.android.chrome` 焦点被判 `on_target=True`，`run_verification` 返回 `completed=True`。
  - 影响：在部分 ROM/Android 版本的 `dumpsys window` 不输出用户前缀时，主空间浏览器窗口会被计为工作资料接管证据，直接破坏"防串味取证"的可信度。
  - 建议：`on_target` 改为 fail-closed——要求 `focus.user_id == target_user.user_id`；`user_id is None` 时记 `unknown` 证据并输出告警，不得据此判定完成。补对应回归用例（`Q-4`）。

### P2（中优先级缺陷，建议本轮或紧随其后的维护版修复）

- **`P2-1` `resolve_activity` / `start_view` 不做任何 shell 转义，安全性靠上游 `extract_url` 隐式兜底。**
  - 位置：`tools/verify_qr_adb.py:463-469`、`:471-477`；兜底依赖 `:2174-2182`。
  - 现状：`&` 安全（单引号 + 无本地 shell，见 §3.1）；单引号逃逸被 `extract_url` 的 `[^\s\"'<>]` 拦住。但方法自身无校验，任一新调用点即可形成设备端注入。
  - 建议：新增 `_shell_quote(value)`（`'` → `'\''`）在两个方法内部强制转义，并对 `;`、反引号、`$` 做显式拒绝或转义。

- **`P2-2` 主空间 ANDROID_ID 为空时隔离校验被空过（fail-open）。**
  - 位置：`tools/verify_qr_adb.py:762-773`（只校验 `target.android_id_valid`）。
  - 影响：`settings get secure android_id` 在 User 0 读取失败（返回 `null`/空）时，只要目标空间 ID 非空即判"已隔离"，隔离结论失去对照基准。
  - 建议：同时要求 `main.android_id_valid`，否则抛 `SafetyError("主空间身份读取失败，无法证明隔离")`。

- **`P2-3` `--user <uid>` 选中非工作资料的普通用户（如访客 User 11）时不告警。**
  - 位置：`tools/verify_qr_adb.py:740-752`。
  - 影响：与 docstring"默认只允许 Island 工作资料"不符；访客空间通常也有独立 ANDROID_ID，能通过隔离校验，从而在错误空间完成验证。
  - 建议：`requested_user` 非主空间且 `is_work_profile` 为假时打印显著警告并要求 `--allow-non-work-user` 显式确认。

- **`P2-4` PNG 解析缺少长度/尺寸防御，恶意或损坏图片会抛非契约异常或触发超大分配。**
  - 位置：`tools/verify_qr_adb.py:856-902`（未校验 IHDR 块长度是否为 13、`body` 是否被截断；`struct.unpack` 失败抛 `struct.error` 而非 `ImageDecodeError`）、`:886-889` 与 `:950`（按 IHDR 的 `width*height` 直接分配，无上限；`zlib.decompress` 无 `max_length`）。
  - 影响：`decode_qr_from_image` 因 `except Exception`（`:1163`）能兜住，但直接调用 `load_gray_image` / `decode_builtin` 的路径会漏出非 `QrDecodeError` 异常；二维码图片属外部不可信输入，存在内存耗尽面。
  - 建议：校验 `length == 13` 且 `body` 完整；限制总像素数（如 40MP）与解压输出上限；统一包装为 `ImageDecodeError`。

- **`P2-5` QR 分段解码的 mode 9（FNC1 第二位置）未读取 8bit 应用指示符。**
  - 位置：`tools/verify_qr_adb.py:2061-2064`（`if mode in (3, 5, 9): if mode == 3: source.read(16)`）。
  - 影响：遇到 FNC1 第二位置符号时位流错位，后续段解析失败或输出错误文本（罕见但属规范偏差）。
  - 建议：`mode == 9` 时补 `source.read(8)`。

- **`P2-6` 运行期 ANDROID_ID 漂移到第三方 uid 仅告警，报告仍按初始快照判 PASS。**
  - 位置：`tools/verify_qr_adb.py:2516-2521`（仅主空间回跳抛错，其余 `writer` 警告）、`:2356-2360`（`isolation_ok` 用初始 `target`/`main` 计算）。
  - 建议：出现任何与初始目标 ID 不一致的漂移即标记报告为"证据失效"，或直接中终止。

- **`P2-7` `ANDROID_ID_RE` 定义后从未使用，`android_id_valid` 接受任意非空字符串。**
  - 位置：`tools/verify_qr_adb.py:493`（`ANDROID_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")` 无引用）、`:226-227`、`:548-557`。
  - 影响：`settings` 返回 `garbage` 等异常串时仍判"有效"，隔离结论被污染；同时该常量是未被使用的死代码。
  - 建议：`parse_android_id` 用 `ANDROID_ID_RE.fullmatch` 收窄，非法值按空值处理。

- **`P2-8` 提交的工具内硬编码个人机器绝对路径。**
  - 位置：`tools/verify_qr_adb.py:46-48`（`ADB_SDK_FALLBACK = Path(r"<sdk-root>\platform-tools\adb.exe")`）。
  - 影响：泄漏本机目录结构，且对其他机器无意义；`resolve_adb_path` 已有 `--adb-path`/`ADB_PATH`/`PATH`/`local.properties` 四条正规来源。
  - 建议：删除该兜底，或改为 `os.environ.get("ANDROID_SDK_ROOT")` 派生。

### Q（疑问 / 验证覆盖缺口）

- **`Q-1`**：缺少"URL 含 `&`/`%`/`;`/`+` 时 `start_view`/`resolve_activity` 生成的命令串"断言用例（正是本次指定的重点核查项）。建议补 2 条，直接断言 `-d '<url>'` 形态与 `&` 未被拆分。
- **`Q-2`**：内置解码器缺 `color_type=6`/`4`（8bit 与 16bit）PNG fixture，导致 `P1-1` 未被现有 45 用例发现。
- **`Q-3`**：`--allow-main-user` 从未被任何用例执行，导致 `P1-2` 未被发现。
- **`Q-4`**：缺"`dumpsys window` 无 `u<uid>` 前缀"的焦点用例，导致 `P1-3` 未被发现。
- **`Q-5`（留待实机）**：`pm list users` 名称/flag 变体、`content query` 是否需要 root、`settings --user N get secure android_id` 在 Island 工作资料下的真实输出、各 ROM `mCurrentFocus` 是否带 `u<uid>`，均只能由主控在**单次常驻进程**的实机上闭环，本轮仅做离线验证。

### N（提示 / 良好实践与现状备注）

1. **`N-1`**：GSF ID 只做展示与对照（`:805-821`、`:2382-2389`），不参与 `assert_space_isolated`。因 ANDROID_ID 是逐用户主身份，此设计可接受；但报告措辞"完全隔离"实际只由 ANDROID_ID 支撑，建议文案改为"ANDROID_ID 隔离通过（GSF 仅供参考）"以免过度承诺。
2. **`N-2`**：本机 `available_backends()` 实测为 `['cv2', 'zxingcpp', 'builtin']`，默认链路先走 `cv2`，因此 `P1-1` 在默认路径被掩盖；只有 `--backend builtin` 或干净环境才暴露。这正是"零依赖兜底"必须自带用例的原因。
3. **`N-3`**：`decode_bmp`（`:1026-1072`）忽略 BMP 文件头的 `bfOffBits` 与 32bpp `BI_BITFIELDS` 掩码，直接以 `header_size` 作为像素起点；探针用 32bpp BITFIELDS DIB 实测仍能解出（3 像素横向偏移被定位与采样容错吸收），属**未被复现的潜在脆弱点**，后续可改为尊重 `bfOffBits`。
4. **`N-4`**：主机侧无 shell 注入面——`default_runner` 全程列表参数、`shell=False`（`:243-266`），URL/路径不经过 cmd/PowerShell 解析。
5. **`N-5`**：`git diff --check` 对两个未跟踪文件等效空跑（`git diff --stat` 为空），该门禁未覆盖本次改动，详见 §2。
6. **`N-6`**：内置解码器为纯 Python 逐像素实现，全分辨率截图（如 1440×3120）会经历 `binarize` 6 轮 + 全行游程扫描，耗时可能达数十秒；当前有 `cv2` 快路径兜底，纯内置环境下建议先做下采样或 ROI 裁剪。
7. **`N-7`**：测试缝设计良好——`runner`/`clock`/`sleeper`/`writer`/`confirm_reader` 全部可注入（`:348-359`、`:2422-2430`、`:2558-2566`），剪贴板通过 monkeypatch 注入，45 用例 `0.40s` 完成且零真实设备/网络访问，符合离线回归要求。

---

## 6. 结论与后续动作

- **最终结论：`PASS`。** 初版发现的 3 项 `P1`（`P1-1` RGBA/LA 通道错位、`P1-2` `--allow-main-user` 死路、`P1-3` 无 `u<uid>` 焦点假 PASS）、8 项 `P2`（`P2-1`~`P2-8`）与 4 项 `Q`（`Q-1`~`Q-4`）已在整改轮全部修复并补齐回归用例；五项门禁实跑全绿（`ruff` / `ruff format` / `pyright` / `pytest 59 passed` / `git add -N` 后的 `git diff --check`），无 `P0`。逐项关闭证据见 §7。
- **实机约束不变**：`Q-5` 的真机验证仍须由主控 Integrator 在**单次常驻进程**内亲自执行，严禁分派子 Agent 或反复重启服务触发 BotGuard 风控；离线门禁通过仅代表工具链代码质量达标，不替代真机取证结论。

---

## 7. 整改闭环与复审结论（2026-11-14）

整改轮仅在授权白名单内修改 `tools/verify_qr_adb.py` 与 `tests/unit/test_verify_qr_adb.py`；未触碰其他业务代码，未执行 `git commit` / `git push`。整改后五项门禁实跑结果：`ruff` = `All checks passed!`、`ruff format --check` = `2 files already formatted`、`pyright` = `0 errors, 0 warnings`、`pytest` = `59 passed in 0.49s`、`git add -N` 后 `git diff --check` = 退出码 `0`（仅 CRLF 换行提示，无空白错误）。

### 7.1 P1 关闭证据（3/3）

| 编号 | 整改实现 | 回归证据 | 状态 |
|---|---|---|---|
| `P1-1` | `_pack_gray_row`（`tools/verify_qr_adb.py:1054-1097`）按 `PNG_CHANNELS` 分派：`color_type=4` 取 `row[x*2]` 灰度样本；`color_type=6` 取 `off=x*4` 并用 `_flatten_alpha`（`:1100-1102`）把 RGB 合成到白色背景；`color_type=2` 保持 `off=x*3`；16bit 先经 `row[0::2]` 抽高字节后共用同一逐样本步长 | `test_decode_builtin_png_rgba_and_la_channels`（`tests:523`，覆盖 RGBA/LA 的 8bit 与 16bit，断言灰度像素逐字节一致且 `decode_builtin` 解出 `QR_B_URL`）；`test_decode_png_rgba_composites_transparency_to_white`（`tests:537`，alpha=0 → 255、alpha=255 保留原值） | **CLOSED** |
| `P1-2` | `assert_space_isolated`（`tools/verify_qr_adb.py:792-825`）新增 `allow_main_user`：主空间模式下只校验目标 ANDROID_ID 有效并放行；`run_verification`（`:2579-2581`）显式透传 `cfg.allow_main_user`；`format_identities`/`VerificationReport.render` 标注“主空间基线模式，无隔离证明” | `test_assert_space_isolated_allow_main_user_checks_only_target`（`tests:452`）；`test_run_verification_allows_main_user_with_explicit_flag`（`tests:816`，实跑 `user_id=0 + allow_main_user=True` 完成并渲染“不适用”） | **CLOSED** |
| `P1-3` | `run_verification`（`tools/verify_qr_adb.py:2661-2681`）改为 fail-closed：`focus_user == target` 才按包名判定；`focus_user is None` 时必须同时存在 `u<target_uid>_a*` 且包名命中 `VERIFY_PACKAGES` 的进程（`target_app_procs`）；其余一律 `on_target=False` | `test_run_verification_does_not_trust_unlabeled_focus_without_processes`（`tests:852`，无 `u<uid>` 且无 `u10_a*` 进程 → `completed=False` 超时）；`test_run_verification_trusts_unlabeled_focus_with_target_processes`（`tests:866`，有进程证据时正常完成） | **CLOSED** |

### 7.2 P2 关闭证据（8/8）

| 编号 | 整改实现 | 回归证据 | 状态 |
|---|---|---|---|
| `P2-1` | `AdbClient._shell_quote`（`tools/verify_qr_adb.py:412-414`）以单引号包裹并把内部 `'` 转义为 `'\''`；`resolve_activity`（`:486-492`）与 `start_view`（`:494-501`）强制调用 | `test_shell_quote_protects_special_urls`（`tests:734`，含 `&`、`%`、`;`、`+` 的 URL 原样进入 `-d '...'`，并断言单引号转义形态） | **CLOSED** |
| `P2-2` | `assert_space_isolated`（`tools/verify_qr_adb.py:800-820`）在隔离模式下同时要求 `main.android_id_valid` 与 `target.android_id_valid`，主空间为空/非法直接 `SafetyError` | `test_assert_space_isolated_rejects_empty_main_android_id`（`tests:447`） | **CLOSED** |
| `P2-3` | `select_work_profile`（`tools/verify_qr_adb.py:758-788`）对显式 `--user <uid != 0>` 且非工作资料的用户要求 `--allow-main-user`，否则 `SafetyError` | `test_select_work_profile_refuses_non_work_secondary_user`（`tests:499`）；`test_run_verification_rejects_non_work_user_without_flag`（`tests:839`） | **CLOSED** |
| `P2-4` | `decode_png`（`tools/verify_qr_adb.py:911-995`）校验 IHDR 长度 `>= 13` 与块边界，宽高 `1..8192`、总像素 `<= 16_777_216`；用 `zlib.decompressobj().decompress(..., expected + 1)` 限制解压输出并检查 `unconsumed_tail`；`struct.error` / `zlib.error` 统一转为 `ImageDecodeError` | `test_decode_png_rejects_truncated_or_oversized_headers`（`tests:558`，覆盖短 IHDR、超大尺寸与解压炸弹） | **CLOSED** |
| `P2-5` | `decode_segments`（`tools/verify_qr_adb.py:2174-2179`）对 mode `9`（FNC1 第二位置）补读 8bit 应用指示符 | `test_decode_segments_mode9_consumes_application_indicator`（`tests:570`，mode 9 + 8bit + byte 段正确解出 `A`） | **CLOSED** |
| `P2-6` | 运行期任何 ANDROID_ID 漂移（含第三方 uid）不再仅告警，而是立即 `SafetyError`（`tools/verify_qr_adb.py:2649-2658`）；前台焦点属于其他用户且为验证相关包名时立即 `SafetyError`（`:2661-2671`） | `test_run_verification_aborts_when_focus_jumps_to_other_user`（`tests:877`，User 11 的 GMS 焦点触发“跳出目标工作资料空间”）；原 `test_run_verification_aborts_on_android_id_rollback` 继续覆盖主空间回跳 | **CLOSED** |
| `P2-7` | `parse_android_id`（`tools/verify_qr_adb.py:571-582`）使用 `ANDROID_ID_RE.fullmatch` 收窄为 16 位十六进制；`SpaceIdentity.android_id_valid` / `same_android_id` 同步收窄并按小写比较 | `test_parse_android_id_normalizes_and_rejects_placeholder`（`tests:328`，拒绝含空格/报错文本/短 ID） | **CLOSED** |
| `P2-8` | 删除硬编码个人绝对路径；新增 `_sdk_roots`（`tools/verify_qr_adb.py:310-326`）依次探测 `ANDROID_SDK_ROOT`、`ANDROID_HOME`、项目相对 SDK 目录与系统标准目录，`resolve_adb_path`（`:328-358`）纳入候选 | `test_resolve_adb_path_uses_android_sdk_root`（`tests:720`，仅设置 `ANDROID_SDK_ROOT` 即可命中 adb） | **CLOSED** |

### 7.3 Q 关闭证据（4/4）

| 编号 | 回归证据 | 状态 |
|---|---|---|
| `Q-1` | `test_shell_quote_protects_special_urls`（`tests:734`）直接断言含 `&`/`%`/`;`/`+` 的 `resolve-activity` 与 `am start` 命令串；`q'` 转义另有断言 | **CLOSED** |
| `Q-2` | `test_decode_builtin_png_rgba_and_la_channels`（`tests:523`，RGBA/LA 8bit+16bit）与 `test_decode_png_rgba_composites_transparency_to_white`（`tests:537`） | **CLOSED** |
| `Q-3` | `test_assert_space_isolated_allow_main_user_checks_only_target`（`tests:452`）、`test_select_work_profile_refuses_non_work_secondary_user`（`tests:499`）、`test_run_verification_allows_main_user_with_explicit_flag`（`tests:816`） | **CLOSED** |
| `Q-4` | `test_run_verification_does_not_trust_unlabeled_focus_without_processes`（`tests:852`）与 `test_run_verification_trusts_unlabeled_focus_with_target_processes`（`tests:866`） | **CLOSED** |

### 7.4 复审判定

- `P0`：无。
- `P1`：`P1-1` / `P1-2` / `P1-3` 全部关闭。
- `P2`：`P2-1` ~ `P2-8` 全部关闭。
- `Q`：`Q-1` ~ `Q-4` 全部关闭；`Q-5` 仍属实机验证项，按 §6 约束由主控在单次常驻进程中执行，不作为代码门禁阻断项。
- **复审结论：`PASS`。**

---

## 附录 A：复现探针与原始输出

探针脚本位于系统临时目录（未入库）：`%TEMP%\kilo\qrrev\probe.py`、`%TEMP%\kilo\qrrev\probe2.py`；均以 `importlib` 只读加载 `tools/verify_qr_adb.py`，不修改任何仓库文件。

**第 1 段（`P1-1`，RGBA/LA PNG）**
```
== probe 1: RGBA / LA PNG builtin decode ==
color_type=6 (RGBA) gray_match=False verdict=DECODE_FAILED out='QrDecodeError: 内置解码器无法识别二维码: 仅找到 0 个定位图形，需要 3 个'
color_type=4 (LA) gray_match=False verdict=DECODE_FAILED out='QrDecodeError: 内置解码器无法识别二维码: 仅找到 0 个定位图形，需要 3 个'
```

**第 2 段（`P1-2`，`--allow-main-user`）**
```
== probe 2: --allow-main-user isolation path ==
selected target = User 0 (Owner)
assert_space_isolated: RAISED -> User 0 的 ANDROID_ID 与主空间 User 0 完全相同（0123456789abcdef），Island 隔离未生效，已中止。
```

**第 3 段（§3.1，`&` 引号安全）**
```
ARGV: ('adb', 'shell', "am start --user 10 -W -a android.intent.action.VIEW -c android.intent.category.BROWSABLE -d 'https://x.test/v?a=1&b=2&c=3'")
ARGV: ('adb', 'shell', "cmd package resolve-activity --brief -a android.intent.action.VIEW -c android.intent.category.BROWSABLE -d 'https://x.test/v?a=1&b=2&c=3' --user 10")
device-shell string: am start --user 10 -W -a android.intent.action.VIEW -c android.intent.category.BROWSABLE -d 'https://x.test/v?a=1&b=2&c=3'
```

**第 4 段（§3.1，`extract_url` 对注入字符的处理）**
```
input='https://x.test/?a=1&b=2' -> 'https://x.test/?a=1&b=2'
input="https://x.test/?a=1'; id; '" -> 'https://x.test/?a=1'
input='https://x.test/?a=1;id' -> 'https://x.test/?a=1;id'
input='https://x.test/?a=1`id`' -> 'https://x.test/?a=1`id`'
```

**第 5 段（`N-3`，32bpp BI_BITFIELDS DIB）**
```
== probe 5: 32bpp BI_BITFIELDS DIB (Windows clipboard common form) ==
raw BITFIELDS DIB decode -> 'https://g.co/verify?c=ZXCVBNM123' (expected 'https://g.co/verify?c=ZXCVBNM123')
```

**第 6 段（`P1-3`，无 `u<uid>` 的假 PASS）**
```
== probe 6: focus without u<uid> prefix is treated as target-space focus ==
package=com.android.chrome focus.user_id=None on_target(uid=10)=True
report.completed=True reason='验证界面已从前台关闭'
focus_trail=['com.android.chrome/com.google.android.apps.chrome.Main', 'com.miui.home/com.miui.home.launcher.Launcher']
```

## 附录 B：门禁命令原文

```
$ uv run ruff check tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py
All checks passed!

$ uv run ruff format --check tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py
2 files already formatted

$ uv run pyright tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py
0 errors, 0 warnings, 0 informations

$ uv run pytest tests/unit/test_verify_qr_adb.py
...........................................................              [100%]
59 passed in 0.49s

$ git add -N tools/verify_qr_adb.py tests/unit/test_verify_qr_adb.py && git diff --check
(退出码 0；仅输出一条 CRLF 提示：warning: in the working copy of 'tools/verify_qr_adb.py',
CRLF will be replaced by LF the next time Git touches it；无空白错误)
```
