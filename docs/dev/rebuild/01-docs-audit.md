# 01 docs/dev 需求漂移审计（调查原文）

- 日期：2026-10-24
- 范围：`docs/dev` 规划/设计/交接文档，对照 `p1-ark-merge` 提交历史
- 状态：只读审计，未修改代码

## 1. 需求时间线

仓库里**没有原始需求原文**；最早的 `24486f8` 已经是计划书。只能由 `PLAN-ark-merge-20261017.md:21-33` 反推原始诉求：free 走 Build、pro/ultra 走 chat、1GB 小鸡多账号、增量移植不重写、不做完整 UI 复刻。

| 时间/提交 | 计划表述 | 实际结果 |
|---|---|---|
| 10-17/18 `24486f8` | 4 项能力 + WP8，WP0–WP11（PLAN:59-73） | 全部落地，另加统计页、日志页、三协议、实例切换；原“非目标 UI 复刻”被 WP7 全量 UI 取代 |
| 10-18 `1cc634e`/`d8bbb22` | WP4 Build 面 + WP5 组/页面配额（PLAN:165-188） | 五相位验收通过，但实例模型随后整体重写 |
| 10-19 `c79f667`/`b59d6f3` | WP5 v3 统一 Context 池，默认 `max_contexts=2`、quota_pool 为资格核心（WP5-V3:238-252） | 交付后即被 V4、10-21 稿推翻 |
| 10-20 `79901b0`/WP5-V4 | 实例=账号会话，同账号 PG+Build 仍是 1 实例（WP5-V4:42-52） | WP7 v2 按此做 UI，10-21 又反转 |
| 10-21 `b1d5f1b`/`a59c885` | 实例=账号+渠道；`max(2,max_contexts)`；只允许 2c1p/1c2p | `9786b3a`（master，非 p1）改为 `max(1,max_contexts)`，重新允许 2c3p |
| 10-21/22 `8dd6782`/`8e5e0cb` | Build 原生 9998 为主；quota 不再 429 | 当前 master 比 p1 多 8 个提交，语义已越过 p1 文档 |

## 2. 矛盾表

| 主题 | 文档 A | 文档 B/实现 | 证据 | 影响 |
|---|---|---|---|---|
| 实例定义 | 实例=账号会话，双渠道仍 1 实例 | 实例=账号+渠道 | WP5-V4:42-52、WP7-V2:35-39 vs SPEC:43-54、AGENT:314；UI 已按后者 `InstanceLimitCard.vue:46`、`schema.ts:603` | 1c2p 无法在 V4 语义下表达，UI/API 反复 |
| 上限与组合 | 只允许 2c1p/1c2p，2c3p 禁止 | `max_instances=max_contexts`；`>=3` 允许 2c3p | `a59c885` 提交正文 vs SPEC:138-152、AGENT:315；同文件 91-93 又把 2c1p 写成 2 context/2 page | 容量验收口径不可复用 |
| 渠道上限 | `max_instances=max_contexts` | 渠道仍有独立 `max_instances`，默认 2/2 | SPEC:150 vs `defaults/config.yaml:92,98`、AGENT:317 | “统一上限”未真正统一 |
| 启动预载 | 按账号启动，每账号默认渠道 1 实例 | 按渠道建槽再补账号 | SPEC:99-125 vs AGENT:337、`instance_manager.py:834-864` | `max_contexts=1` 只会起一个渠道，与文档不符 |
| Build 路径 | signer 直连为主，9998 桥 403 为开放项 | native 9998 为主，signer 仅备用 | PLAN:154-162、SPIKE:53-78、107-114 vs AGENT:295、`build_surface.py:367-377`；`8dd6782` | P3 验收证据全部测的是旧主路径 |
| Build 预算错误码 | `surface_quota_exceeded` 429 | 统一 503；build 容量改 `surface_instance_busy` | PLAN:223、WP5-V3:284 vs `surface_orchestrator.py:62`、`routing_pipeline.py:443-448`；`8e5e0cb` 明写“不再映射 429” | 客户端重试/退避语义改变 |
| 24h 保活 | 回写所有存活槽位 cookies | 仅 `chat_ready` 槽位回写 | WP5-V3-IMPL:46、PLAN:324 vs `client.py:187-197` | Build-only 实例存在保活盲区 |
| quota_pool | 额度池是资格核心 | 已废弃，改账号 tag | PLAN:249、WP5-V3:249 vs AGENT:323、`channel_registry.py:204-207`、`routing_eligibility.py:197` | 旧策略组配置语义失效 |
| UI 切换入口 | 新建实例只选账号，无渠道 | 面板含渠道按钮，新建实例选账号+渠道 | WP7-V2:166-186 vs SPEC:250-281、`InstanceSwitchModal.vue:232-263,576` | 两套 UI 稿不能并存 |
| 自动切号 | 必须保留并接线 | AUDIT 判为死开关 | PLAN:319-328 vs AUDIT:175；现由 `account_keepalive.py:357-374` 接 rebalance | 已解决，但 AUDIT 未标废弃 |

## 3. 权威与废弃

**当前权威**：代码 + `AGENT.md` §5–§8（尤其 `294-323`）。`INSTANCE-SWITCH-SPEC` 仅作 UI/交互口径，其中启动段（99-125）和渠道上限段（138-152）应按 AGENT/code 修正。`defaults/config.yaml` 的 quota_pool 注释（84-85）和渠道 2/2（92,98）是残留。

**应废弃/合并**：WP5-V4、WP7-V2（被 10-21 反转）；WP7-UI-DESIGN（v3 槽位 UI）；UI-CHANNEL（quota_pool/槽位）；WP5-V3 设计+实现计划（默认 2 Context/quota_pool）；两个 HANDOFF（仍称 v3/实例=账号）；PLAN/SPIKE/AUDIT 仅历史证据；WP6 两份与统计实现重复，现行口径在 AGENT §7。

**未实现或相反**：429→503、quota_pool 废弃、`max_instances=max_contexts` 未落到渠道配置、按账号启动未实现、24h 全槽位回写未实现、signer 主路径已反转、V4 实例=账号已反转、2c3p 从禁止改为允许。

## 4. 最危险的 3 个“文档驱动偏航点”

1. **用内部 Context/Page/Slot 直接设计用户 UI**：WP5-V3:52-60 → WP7-UI-DESIGN:13-17,180-184 → WP7-V2:13-24 → 10-21 稿，连续三次 UI 推倒重来。
2. **容量代数与错误码在“验收通过”后反复改**：`a59c885` `max(2,..)` → `9786b3a` `max(1,..)` + 2c3p → `8e5e0cb` 429→503；旧验收、旧测试、旧文档不能再引用。
3. **Build 传输主备反转，且保活只覆盖 chat**：PLAN/SPIKE 让人去调 signer/403 旧路，实际已是 native 9998 主路径（AGENT:295）；同时 Build-only 槽位不回写 cookie（`client.py:192`），账号保活存在静默缺口。
