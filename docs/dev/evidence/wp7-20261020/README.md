# WP7 UI 验收证据（2026-10-20）

- 分支：`p1-ark-merge`
- 设计稿：`docs/dev/WP7-UI-DESIGN-20261020.md`
- 后端：`http://127.0.0.1:8080`
- 前端开发服务：`http://127.0.0.1:3000`
- 浏览器：Playwright Chromium，浅色主题，免密模式
- 本地配置：`config.yaml`（gitignored）；多模式截图使用临时修改，测试后已恢复单模式

## 已提交

```text
c7bdd80 feat(ui): 完成 WP7 日志页与统计页收尾
fe37d9b feat(ui): 完成 WP7 系统配置深度分区
7ac7a1a feat(ui): 完成 WP7 账号管理多模式与批量导入
fd15dbf feat(ui): 完成 WP7 控制面板改版
eed30b6 feat(logs): 新增日志 SSE 流与 7 天归档导出
50e57c6 docs(ui): 落盘 WP7 UI 统一设计稿
```

## 质量门结果

后端：

```text
uv run pytest tests/unit -q           840 passed
uv run ruff check .                   All checks passed
uv run ruff format --check .          203 files already formatted
bun x pyright src tests               0 errors, 0 warnings, 0 informations
```

前端：

```text
cd web
bun run type-check                    通过
bun run lint                          通过（0 warnings）
bun run build                         通过，已重建 src/aistudio_api/static/
```

## 截图

### P1 控制面板 `/`

- `p1-desktop-1440.png`：1440×900，正常态
- `p1-tablet-1024.png`：1024×768，正常态
- `p1-mobile-390.png`：390×844，正常态；模型表为卡片，无竖排表头
- `p1-instances-open-1440.png`：实例详情展开态

### P2 账号管理 `/accounts`

- `p2-single-1440.png`、`p2-single-1024.png`、`p2-single-390.png`：单模式
- `p2-multi-1440.png`、`p2-multi-1024.png`、`p2-multi-390.png`：真实临时多模式配置
- `p2-matrix-1440.png`：真实多模式渠道矩阵展开
- `p2-drag-overlay-1440.png`、`p2-import-preview-1440.png`：批量导入预览/拖拽覆盖层
- `p2-empty-1024.png`、`p2-error-1024.png`：空态/错误态
- 说明：`p2-matrix-1024.png`、`p2-matrix-390.png`、`p2-matrix-custom-1024.png`、`p2-slot-popover-1024.png`、`p2-import-tier-pending-1024.png`、`p2-import-tier-failed-1024.png` 来自开发期 route mock，作为交互补充，不作为真实账号状态证明。

### P3 系统与模型配置 `/settings`

- `p3-collapsed-1440.png`：深度功能关闭
- `p3-expanded-1440.png`：深度功能开启，顶部基础区
- `p3-advanced-1440.png`：渠道注册表、策略组、槽位表
- `p3-routing-1440.png`：路由安全与分组编排摘要
- `p3-advanced-1024.png`、`p3-advanced-390.png`：深度区响应式；390 下渠道卡片化、保存按钮 sticky

### P4 用量统计 `/stats`

- `p4-stats-1440.png`、`p4-stats-1024.png`、`p4-stats-390.png`
- 说明：7 天范围当前无日聚合数据，页面按设计显示空态与 `—`；账号调度面板展示当前状态。

### P5 日志 `/logs`

- `p5-live-1440.png`、`p5-live-1024.png`、`p5-live-390.png`：真实 SSE 实时流
- `p5-paused-1440.png`：暂停态
- `p5-export-panel-1440.png`：按时间导出面板

真实导出验证：

- 点击“最近 1 天”下载 `aistudio-logs-20261020-202830.log`，约 1.98MB
- 点击“最近 3 天”下载 `aistudio-logs-20261020-202834.zip`
- ZIP 内包含 `aistudio-20261020.log`
- 日志首行示例：`2026-10-20 19:09:04 INFO aistudio.model_discovery ...`

## 交互验证

已真实点击/验证：

- P2 渠道矩阵展开、单/多模式切换后的页面布局
- P5 暂停/继续、导出面板、最近 1 天 `.log` 下载、最近 3 天 `.zip` 下载
- P1 实例详情展开、OpenAI Chat 格式 tab 切换
- P3 深度开关、深度区滚动与移动端布局

尚未逐个点击验证：

- P1 复制、查看统计、429/错误跳日志、前往账号管理
- P2 文件选择/拖拽最终导入、槽位绑定、恢复继承、逐格渠道开关
- P3 组序调整、槽位增删、保存/丢弃
- P4 时间范围与图表交互
- P5 断线重连、清屏、下载当前、过滤空态

## 已知限制 / 未完成证据

- P1 加载态、错误态、空数据态未单独截图。
- P5 断流/重连、过滤空态、移动端暂停态未单独截图；SSE 断线重连未实测。
- P2 多模式 1024/390 已用真实临时配置补拍，但槽位绑定/矩阵开关未做最终页面点击回归。
- P2 导入的多文件选择与拖拽流程在开发期使用 mock 预览验证，最终页面尚未用真实文件重新走一遍。
- P3 console warning 修复后未重新记录 console 日志。
- 上述未验证项不影响已提交的页面与接口，但正式 push 前建议补做。

## 配置恢复说明

P3 开发期一次保存曾重写本地 `config.yaml`（注释被 YAML.stringify 剥离）。随后已用 `defaults/config.yaml` 恢复。

P2 多模式截图使用临时槽位：

```yaml
features:
  channels:
    build:
      enabled: true
  slots:
    - { account_id: acc_46223636, channel: chat, enabled: true, note: pro-chat }
    - { account_id: acc_b1a36ccf, channel: build, enabled: true, note: free-build }
```

截图后已恢复为 `slots: []`、`build.enabled: false` 并热重载。备份仍保留在：

```text
%TEMP%\config.yaml.wp7-multi2.bak
%TEMP%\config.yaml.wp7-multi.bak
%TEMP%\config.yaml.wp7-p3.bak
```
