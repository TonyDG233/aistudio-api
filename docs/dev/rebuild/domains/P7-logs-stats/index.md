# P7-logs-stats / 日志、脱敏与单一统计账本域

- 域标识：`P7-logs-stats`（已归并原 `P7-logs`）
- 核心职责边界：
  - 日志子系统（D09）：三 Sink 统一管道（stderr + 5000 容量 ring 广播 + 按日 7 天归档）、root DEBUG + WARP/tunnel/CDP/httpx 噪音过滤、`/logs/stream` SSE（15s 心跳、gap/dropped 事件）与 1/3/7 天导出、`safe_*` 脱敏 dump（默认关闭，严禁明文 headers/raw_body/cookie/token/key）。
  - 统计子系统（D10）：单一事件账本（client 终态与 attempt 重试分母严格分离，绝不混算成功率）、90 天 `daily` / `by_model`（Asia/Shanghai）、`countTokens` 独立账本（不计入生成配额）、趋势图表与配置 Tooltip 防溢出。
- 核心代码模块：
  - `src/aistudio_api/infrastructure/utils/logger.py`
  - `src/aistudio_api/infrastructure/utils/log_broadcast.py`
  - `src/aistudio_api/api/routes_logs.py`
  - `src/aistudio_api/api/state.py`（统计事件账本与 `build_stats_payload`）
  - `web/src/views/LogsView.vue`、`web/src/components/dashboard/{UsageRequestChart,UsageTokenChart,ModelUsageChart}.vue`、`web/src/components/config/FieldHelpTip.vue`

## 任务计划与里程碑看板

| 里程碑 / 任务 | 核心目标与交付物 | 状态 | 对应审查报告 |
|---|---|---|---|
| **B4 P7/D09 日志、SSE 与脱敏归档** | 三 Sink、5000 ring、7 天按日归档、SSE 广播与导出、`safe_*` dump、LogsView 虚拟滚动与本地日边界导出 | 已完成（PASS） | `reviews/REV-B4-p7-logs.md` |
| **C4 单一统计事件账本与趋势图** | client/attempt 分母分离、`countTokens` 独立账本、90 天 daily/by_model、列表/趋势双视图与 3 段堆叠图 | 已完成（PASS） | `reviews/REV-C4.md` |
| **C4b 配置 Tooltip 防溢出与账本一致性** | `FieldHelpTip` 视口 clamp 与 75 词条推荐值对齐、沙盒 daily/by_model 恒等式、client Tooltip 语义修正 | 已完成（PASS） | `reviews/REV-C4b.md` |
| **后续演进：tool-use prompt 分项与持久筛选** | 评估将 `toolUsePromptTokenCount` 单列与趋势视图筛选状态持久化 | 待评估 | — |

## `reviews/` 审查报告与截图索引

### 审查报告
- [`reviews/REV-B4-p7-logs.md`](reviews/REV-B4-p7-logs.md) — B4 日志三 Sink、SSE、导出与 dump 安全审查（2026-10-30，PASS）
- [`reviews/REV-C4.md`](reviews/REV-C4.md) — C4 统计事件账本与趋势图终审（2026-11-08，PASS）
- [`reviews/REV-C4b.md`](reviews/REV-C4b.md) — C4b 配置 Tooltip 防溢出与沙盒恒等式终审（2026-11-08，PASS）

### 截图资产（`reviews/assets/`）
- [`reviews/assets/c4-dashboard-trend-charts-1440.png`](reviews/assets/c4-dashboard-trend-charts-1440.png) — 1440 趋势图双图全景（30 天 client 模式）
- [`reviews/assets/c4-dashboard-trend-tooltip-1440.png`](reviews/assets/c4-dashboard-trend-tooltip-1440.png) — 1440 Tokens 堆叠图深色毛玻璃 Tooltip
- [`reviews/assets/c4b-settings-tooltip-1440.png`](reviews/assets/c4b-settings-tooltip-1440.png) — 1440 设置页 Tooltip 视口防溢出
- [`reviews/assets/c4b-settings-tooltip-1024.png`](reviews/assets/c4b-settings-tooltip-1024.png) — 1024 设置页 Tooltip 视口防溢出
- [`reviews/assets/c4b-settings-tooltip-768.png`](reviews/assets/c4b-settings-tooltip-768.png) — 768 设置页 Tooltip 视口防溢出
- [`reviews/assets/c4b-settings-tooltip-375.png`](reviews/assets/c4b-settings-tooltip-375.png) — 375 移动端设置页 Tooltip 视口防溢出
