# D04 Instance / Pool / Lease / Capacity

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D04 | manager 记录（账号+channel、稳定编号）与 pool slot（Context 单位）分层 | 79901b0; instance_manager.py:78-113,738-750; instance_pool.py:49-78 | 新增 | working | test_instance_manager.py:266-283 | keep | 待用户决定 |
| D04 | manager 记录 runtime_ref 绑定 slot_id，孤儿 pool slot 收养为实例 | 79901b0; instance_manager.py:936-968 | 新增 | partial | test_instance_manager.py:439-467；同账号额外 slot 不收养，跨账号收养未验证 | keep | 待用户决定 |
| D04 | 账号+channel 唯一性，重复创建返回 already_exists | a59c885; instance_manager.py:270-273,774-783 | 新增 | working | test_instance_manager.py:352-361 | keep | 待用户决定 |
| D04 | max_contexts 统一为实例上限（manager）与 Context 上限（pool） | 9786b3a; instance_manager.py:809-810; instance_pool.py:202-211 | 改造 | working | 9786b3a diff max(2)→max(1)；test_instance_manager.py:667-689 | keep | 待用户决定 |
| D04 | 一账号一 Context：同账号 slot 复用，不新建 Context | 1201a37; instance_pool.py:533-606,1051-1094 | 新增 | working | test_instance_pool.py:124-143,799-832 | keep | 待用户决定 |
| D04 | create_instance 同账号第二 channel 复用 Context，仅加 channel 页 | a59c885; instance_pool.py:546-588,642-687 | 新增 | working | test_instance_pool.py:811-832,913-934 | keep | 待用户决定 |
| D04 | chat+build 两页共存于同一 Context（偏差：一 Context 两 page） | 1201a37,a59c885; instance_pool.py:642-687,1051-1094 | 新增 | working | test_instance_pool.py:204-226,811-832；与“一 context 一 page”目标冲突 | port（需拆分 Context） | 待用户决定 |
| D04 | build-only：加载后关闭 bootstrap chat 页，保留 Context/cookie | b59d6f3; instance_pool.py:1028-1038 | 新增 | working | test_instance_pool.py:890-909 | keep | 待用户决定 |
| D04 | create_instance 无同账号 slot 时新建 slot 并 pinned | 79901b0,a59c885; instance_pool.py:533-606 | 新增 | working | test_instance_pool.py:746-759,799-807 | keep | 待用户决定 |
| D04 | startup 每启用 channel 先占一槽 | da6ea25,7cd6d74; instance_manager.py:834-864 | 新增 | working | test_instance_manager.py:300-313 | keep | 待用户决定 |
| D04 | startup 按剩余预算补槽到 max_contexts，受 channel cap 限制 | 7cd6d74; instance_manager.py:844-864 | 改造 | working | test_instance_manager.py:623-638 | keep | 待用户决定 |
| D04 | startup 活跃账号优先排序 | da6ea25; instance_manager.py:127-137 | 新增 | working | test_instance_manager.py:300-313 | keep | 待用户决定 |
| D04 | startup 走 eligibility/group/affinity 选号 | 7cd6d74; instance_manager.py:875-903 | 新增 | working | test_instance_manager.py:606-654 | keep | 待用户决定 |
| D04 | startup 失败账号隔离，失败后少启动不整体失败 | 7cd6d74; instance_manager.py:148-225 | 新增 | working | test_instance_manager.py:316-349 | keep | 待用户决定 |
| D04 | startup 同账号多 channel 共享一个 Context，产生两条实例记录（偏差） | da6ea25,a59c885; instance_manager.py:165-217 | 改造 | partial | test_instance_manager.py:316-333；记录数≠Context 数 | port（记录应绑页） | 待用户决定 |
| D04 | create 前预检全局/渠道/页面预算，不产生半实例 | da6ea25; instance_manager.py:278-287,696-705; instance_pool.py:608-640 | 新增 | working | test_instance_manager.py:372-381；test_instance_pool.py:980-989 | keep | 待用户决定 |
| D04 | create 异步 STARTING，失败移除记录并回滚 current/latest | 79901b0; instance_manager.py:285-288,528-534,707-717 | 新增 | working | test_instance_manager.py:439-453 | keep | 待用户决定 |
| D04 | close STARTING 无 runtime 时直接取消任务并删记录 | da6ea25; instance_manager.py:295-302 | 新增 | unverified | 仅代码路径，无对应测试；未验证 | keep | 待用户决定 |
| D04 | close ACTIVE 转 RECLAIMING 并由后台 reclaim 关实例 | da6ea25; instance_manager.py:307-311,621-639 | 新增 | working | test_instance_manager.py:384-399 | keep | 待用户决定 |
| D04 | acquire 预留：reservations 计数、BUSY 状态、condition 通知 | 1201a37; instance_pool.py:411-531,1051-1094 | 新增 | working | test_instance_pool.py:124-143,585-587 | keep | 待用户决定 |
| D04 | 同账号同 surface 优先复用现成 slot 与页 | 1201a37; instance_pool.py:1051-1094 | 新增 | working | test_instance_pool.py:124-143,204-226 | keep | 待用户决定 |
| D04 | acquire 新建 slot，满额时 rebind 最久未用 idle slot | dd5e359; instance_pool.py:854-917 | 新增 | working | test_instance_pool.py:147-164,635-656 | keep | 待用户决定 |
| D04 | 全局容量：slot 数受 max_contexts 限制，超时抛 InstanceCapacityError | 1201a37; instance_pool.py:490-502,896-917 | 新增 | working | test_instance_pool.py:184-200 | keep | 待用户决定 |
| D04 | 全局 page 预算=max_contexts（不再独立 global_max_pages，偏差：页数=实例数） | dd5e359,9786b3a; instance_pool.py:333-363 | 改造 | working | test_instance_pool.py:660-673,938-959 | port（页与实例解耦） | 待用户决定 |
| D04 | channel page 预算由 max_instances 派生，chat/build 一页一实例 | 7cd6d74; instance_pool.py:314-331 | 改造 | working | test_instance_pool.py:591-632 | keep | 待用户决定 |
| D04 | group page 预算按 group.max_pages 拒绝超额 | dd5e359,b59d6f3; instance_pool.py:269-278,355-362 | 新增 | working | test_instance_pool.py:676-701 | keep | 待用户决定 |
| D04 | channel_max_instances 渠道实例硬上限（0 表示禁用） | 7cd6d74; instance_pool.py:288-301; instance_manager.py:812-818 | 改造 | working | test_instance_manager.py:623-665 | keep | 待用户决定 |
| D04 | channel_enabled：build 回退 features.build_surface | 7cd6d74; instance_pool.py:303-312 | 改造 | working | test_features_routing_config.py:24-53 | keep | 待用户决定 |
| D04 | pending_pages/pending_channel 占位计数，避免 build 并发双计页 | a59c885; instance_pool.py:232-255 | 改造 | working | test_instance_pool.py:229-261,529-538 | keep | 待用户决定 |
| D04 | borrow：容量失败后借用其他账号 idle slot 的现成页 | 1201a37; instance_pool.py:1096-1131 | 新增 | working | test_instance_pool.py:541-559；test_routing_pipeline.py:429-446 | keep | 待用户决定 |
| D04 | stale slot 复活：浏览器回收后同账号复用原 slot 重载 | 86391c8; instance_pool.py:919-961 | 新增 | working | test_instance_pool.py:1034-1058 | keep | 待用户决定 |
| D04 | pinned slot 不参与 rebind、borrow、idle retire | 79901b0; instance_pool.py:76,854-868,1188 | 新增 | working | test_instance_pool.py:746-759 | keep | 待用户决定 |
| D04 | SlotState 状态机含 COOLDOWN，但无任何赋值点 | 1201a37; instance_pool.py:36-42 | 新增 | legacy | 引用见 instance_pool.py:239,791,867,1189；无 COOLDOWN 赋值，未验证 | drop（或补状态转换） | 待用户决定 |
| D04 | _load_slot 冷启动重建 Context，最多 3 次尝试 | b59d6f3; instance_pool.py:963-1017 | 新增 | unverified | 无单测覆盖 3 次重试；未验证 | keep | 待用户决定 |
| D04 | release 只减 reservation、touch last_used，不关页不 dispose | 1201a37; instance_pool.py:832-850 | 新增 | working | test_instance_pool.py:516-526 | keep | 待用户决定 |
| D04 | close_channel 只关指定 channel 页，保留 Context 与另一页 | a59c885; instance_pool.py:689-719 | 新增 | working | test_instance_pool.py:835-857 | keep | 待用户决定 |
| D04 | close_instance 置 RETIRING、移出池并 dispose Context | 79901b0; instance_pool.py:739-750,1214-1231 | 新增 | working | test_instance_pool.py:762-775 | keep | 待用户决定 |
| D04 | reassign_instance 保留 slot 身份与 pin，换账号后重载 | 79901b0; instance_pool.py:752-771 | 新增 | working | test_instance_pool.py:778-795 | keep | 待用户决定 |
| D04 | rebind 空闲 slot 换账号/组，generation 自增并重建 Context | dd5e359; instance_pool.py:773-830 | 新增 | working | test_instance_pool.py:704-742 | keep | 待用户决定 |
| D04 | retire_idle 空闲回收 API 存在但无 src 调用方 | 1201a37; instance_pool.py:1180-1205 | 新增 | legacy | test_instance_pool.py:562-574；src 无调用，未验证 | drop（或接线） | 待用户决定 |
| D04 | slots/pages/budget snapshot 供路由与诊断 | dd5e359,86391c8; instance_pool.py:364-407 | 新增 | working | test_instance_pool.py:577-587；routing_pipeline.py:581-582 | keep | 待用户决定 |
| D04 | has_active_work 阻止浏览器空闲回收处于 LOADING/BUSY 的实例 | 86391c8; instance_pool.py:364-371 | 新增 | working | app.py:185-187 | keep | 待用户决定 |
| D04 | _runtime_occupancy 统计辅助无调用方 | 79901b0; instance_manager.py:912-919 | 新增 | legacy | 全仓无引用，未验证 | drop | 待用户决定 |
| D04 | generation/realm_id 随重载自增，旧 realm 触发 retire 回调 | 1201a37; instance_pool.py:80-84,982-984,986-1023 | 新增 | working | test_instance_pool.py:147-164,562-574 | keep | 待用户决定 |
| D04 | switch_to 当前实例原地切换，不动其他实例 | da6ea25; instance_manager.py:334-340 | 新增 | working | test_instance_manager.py:469-496 | keep | 待用户决定 |
| D04 | switch_to 复用同账号 sibling channel 实例 | a59c885; instance_manager.py:342-352 | 新增 | working | test_instance_manager.py:525-539 | keep | 待用户决定 |
| D04 | switch_to 容量满时关闭原 channel 并在同 Context 开新 channel | 9786b3a; instance_manager.py:354-409 | 改造 | working | test_instance_manager.py:666-690 | keep | 待用户决定 |
| D04 | switch_to 换 channel 失败回滚恢复原 channel | 9786b3a; instance_manager.py:411-429 | 改造 | working | test_instance_manager.py:692-715 | keep | 待用户决定 |
| D04 | switch_to 无效实例重启，容量不足时关闭 victim | da6ea25; instance_manager.py:604-619 | 新增 | working | test_instance_manager.py:557-604 | keep | 待用户决定 |
| D04 | switch_to 目标账号已是 active 时跳过蓝绿账号切换 | 86391c8; instance_manager.py:455-464 | 改造 | working | test_instance_manager.py:743-774 | keep | 待用户决定 |
| D04 | reassign 拒绝迁移多 channel 实例之一，避免拆散共享 Context | a59c885; instance_manager.py:474-477 | 新增 | unverified | test_instance_manager.py:402-421 未覆盖 sibling 分支；未验证 | keep | 待用户决定 |
| D04 | in-flight 防换号：rebind/close_instance/close_channel/manager close/reassign 拒绝 | 1201a37,79901b0,a59c885; instance_pool.py:698-699,745-746,789-790; instance_manager.py:303-306,480-483 | 新增 | working | test_instance_pool.py:728-742,762-775；test_instance_manager.py:384-399 | keep | 待用户决定 |
| D04 | build 在途禁止换账号/重载，抛 BUILD_BUSY | 8e5e0cb; build_surface.py:1002-1007 | 改造 | working | test_build_surface.py:528-537 | keep | 待用户决定 |
| D04 | build 请求身份取 lease.auth_file/auth_user，不回退全局 session | 8e5e0cb; client.py:158-178,588,698 | 改造 | working | test_build_lease_identity.py:10-39 | keep | 待用户决定 |
| D04 | acquire 首试 30s、切号后 15s，超时映射容量错误 | 8e5e0cb; routing_pipeline.py:430-442 | 改造 | working | test_routing_pipeline.py:429-446 | keep | 待用户决定 |
| D04 | 容量错误码：chat=surface_quota_exceeded，build=surface_instance_busy，统一 503 | 8e5e0cb; routing_pipeline.py:443-487 | 改造 | working | test_generation_routing.py:196-235 | keep | 待用户决定 |
| D04 | 单实例 failover 限制：无空闲 slot 时不跨账号抢占，只能等待/defer/rebind | 1201a37,8e5e0cb; instance_pool.py:333-335,490-502; routing_pipeline.py:443-487 | 新增 | partial | test_instance_pool.py:938-1009；test_generation_routing.py:157-235；跨号切换需先释放 | port（一 context 一页时需重设计） | 待用户决定 |
| D04 | 容量满时 defer 等待后重试同候选 | 86391c8; generation_service.py:201-250; routing_safety.py:25-71 | 新增 | working | test_generation_routing.py:157-235 | keep | 待用户决定 |
| D04 | InstanceSwitcher 只规划 idle slot，最受限 slot 优先，跳过 in_flight | dd5e359; instance_switch.py:71-137 | 新增 | working | test_instance_switch.py:52-96 | keep | 待用户决定 |
| D04 | InstanceSwitcher 以 pool.rebind 应用迁移并带组页预算 | dd5e359; instance_switch.py:173-216 | 新增 | working | test_instance_switch.py:99-171 | keep | 待用户决定 |
| D04 | 实例记录数参与容量判定，与 Context 数/页数不等价（偏差） | da6ea25; instance_manager.py:905-919; instance_pool.py:215-221,561-565 | 改造 | partial | test_instance_manager.py:316-333；2 记录共享 1 context | port（容量按页计） | 待用户决定 |
| D04 | 单实例默认配置 max_contexts=1，亦为启动预启动数量 | 79901b0,9786b3a; defaults/config.yaml:110-119 | 改造 | working | test_features_routing_config.py:24-53；test_instance_manager.py:667-689 | keep | 待用户决定 |

## 冲突/缺口

| 冲突/缺口 | 说明 | 证据 |
|---|---|---|
| 一 Context 一 Page vs 同账号双页 | 同账号 chat+build 共用一个 Context 且并存两页，直接偏离目标 | instance_pool.py:642-687; test_instance_pool.py:204-226,811-832 |
| max_contexts 语义重叠 | 同时是实例记录上限、Context 上限、全局 page 预算 | instance_manager.py:809-810; instance_pool.py:202-211,333-335 |
| 实例数≠Context 数≠Page 数 | startup 同账号双 channel 产生 2 条记录、1 个 Context；容量判定混用 | instance_manager.py:905-919; test_instance_manager.py:316-333 |
| 单实例 failover 受限 | max_contexts=1 时页预算=1，chat/build 互斥；跨账号只能 defer 或等释放 | instance_pool.py:333-335,490-502; test_instance_pool.py:938-1009 |
| 死状态/死 API | COOLDOWN 无赋值；retire_idle 无 src 调用；_runtime_occupancy 无引用 | instance_pool.py:36-42,1180-1205; instance_manager.py:912-919 |
| 在途检查非原子 | manager 先读 in_flight 再调用 pool close/rebind，中间存在竞态窗口 | instance_manager.py:303-306,367-376,480-483; instance_pool.py:698-699,745-746 |
| reassign sibling 守卫无测试 | 多 channel 实例拒绝迁移分支缺少回归证据 | instance_manager.py:474-477; test_instance_manager.py:402-421 |
| build-only bootstrap 页占预算 | 装载期 chat bootstrap 页短暂计入 chat 页预算 | instance_pool.py:1028-1038; test_instance_pool.py:270-303 |
