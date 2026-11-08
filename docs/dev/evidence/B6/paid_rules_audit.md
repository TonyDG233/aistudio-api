# B6 Paid Model Rules 审计（Q1/Q2 裁决后）

范围：只读核对 `paid_model_rules` 默认值、匹配语义，以及静态规则与动态发现
（live/snapshot）的优先级。未启动服务、浏览器或真实网络请求；结论基于
`docs/dev/evidence/B6/model_discovery_fix.json` 快照与离线重算。

## 1. 默认规则与来源

| 位置 | 内容 |
|---|---|
| `defaults/config.yaml:87-93` | `ultra_required.contains = ["veo","ultra"]` → `["Ultra","Ultra_20X"]`；`pro_required.contains = ["argon","gemini-4","imagen"]` → `["Pro","Ultra","Ultra_20X"]` |
| `src/aistudio_api/infrastructure/gateway/model_defaults.py` | 代码内默认回退与上面完全一致（已移除裸 `image`） |
| `src/aistudio_api/infrastructure/gateway/model_defaults.py` | `get_configured_paid_model_rules()`：优先 config，缺失时回退代码默认 |
| `src/aistudio_api/application/routing_eligibility.py` | `_rule_matches()`：`contains` 小写子串、`prefixes` 前缀、`exact` 全等 |
| `src/aistudio_api/application/routing_eligibility.py` | `resolve_required_tiers()` / `resolve_model_route()` 优先级见第 2 节 |

历史来源：

- `99c38ba`（account grouping / preset / quota / multi-tier preemption）首次在
  `defaults/config.yaml` 引入 `paid_model_rules` 默认块。
- `a9e7dc3` 在 `model_defaults.py` 增加 `paid_model_rules` 类型校验，并把默认块
  保留在代码内默认配置。

## 2. Q1 裁决后的优先级

1. 模型 ID 先 `normalize_model_key()`（去 `models/`、去 chat/build 等 channel
   前缀、转小写）。
2. **真实动态真值优先**：`model_discovery.get_cached_model_info(clean)` 带
   live/snapshot 来源标记（`live_page` / `live_http` / `snapshot`）时：
   - `paid is False` 且 `accessModes` 不含 3/4 → **Free，返回 `()`**，不再应用
     静态 `paid_model_rules`；
   - `paid is True` 或 `accessModes` 含 3/4 → 视为 live Paid，继续应用静态规则
     （静态规则可给出更严格的 Ultra 要求）；无规则时按动态 Paid → Pro+。
3. 模型缺失、来源为 `_FALLBACK_MODELS`（`fallback`）或来源未知时，按历史顺序
   应用静态规则：`ultra_required` → Ultra/Ultra_20X；
   `pro_required` → Pro/Ultra/Ultra_20X。
4. 仍未定 → 动态 `accessModes` / `paid` → `is_paid_model()` 全局 Paid 集合 →
   Free。
5. `resolve_model_route().paid` 跟随上述结果：live Free 模型强制
   `paid=False`、`lane="free"`，即使它命中静态子串规则或仍在 Paid 种子里。

## 3. 证据与离线重算

| 模型 | 动态发现（`model_discovery_fix.json`） | 裁决后路由 |
|---|---|---|
| `gemini-3.1-flash-lite-image` | `paid=false`、`access_tier=1`、`accessModes=[]` | **Free**（Q1：live Free 覆盖静态 `image`；且裸 `image` 已移除） |
| `gemini-3.1-pro-preview` | `paid=false`、`access_tier=null` | **Free**（Q2：名称含 `pro` 不构成 Paid） |
| `antigravity-preview-09-2026` | `paid=true`、`access_tier=2`、`accessModes=[1]` | Paid → Pro/Ultra/Ultra_20X |
| `gemini-3.6-flash` | `paid=true`、`access_tier=2`、`accessModes=[1,3]` | Paid → Pro/Ultra/Ultra_20X |
| 其余 live Paid（deep-research / gemini-4-argon / nano-banana / image / lyria / omni） | `paid=true` | Paid → Pro/Ultra/Ultra_20X |

离线测试覆盖：

- `tests/unit/test_model_discovery_live_wiring.py`：
  `test_live_free_truth_overrides_static_image_rule`、
  `test_live_paid_truth_from_evidence_uses_paid_lane`、
  `test_live_free_gemini_pro_preview_is_not_paid_from_name`、
  `test_fallback_free_is_not_a_live_free_truth`。
- `tests/unit/test_model_access.py`：
  `test_default_paid_seeds_cover_every_live_paid_model`。

## 4. 结论

- 静态 `image` 子串不再覆盖真实 live/snapshot Free 结果；真实 Free 模型不会被
  标记为 Paid 或送入 paid lane。
- fallback/unknown 仍保留静态规则作为冷启动保护，不会被误判为 live Free。
- 所有 31 条 live 证据条目都有显式 `paid` 字段；16 条 `_FALLBACK_MODELS` 均有
  显式 `paid`（`gemini-3.5-flash` 已补 `paid: false`）。
- 本次未新增服务/浏览器/网络行为，也未提交。
