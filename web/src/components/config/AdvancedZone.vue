<script setup lang="ts">
import type {
  ChannelsConfig,
  FeaturesConfig,
  RoutingConfig,
} from './types.ts'
import ChannelRegistryCard from './ChannelRegistryCard.vue'
import RoutingSafetyCard from './RoutingSafetyCard.vue'
import AdvancedProtocolCard from './AdvancedProtocolCard.vue'

const props = defineProps<{
  features: FeaturesConfig
}>()

const emit = defineEmits<{
  'update:features': [val: FeaturesConfig]
}>()

function patch(patchValue: Partial<FeaturesConfig>) {
  emit('update:features', { ...props.features, ...patchValue })
}

function updateChannels(val: ChannelsConfig) {
  patch({ channels: val })
}

function updateRouting(val: RoutingConfig) {
  patch({ routing: val })
}
</script>

<template>
  <div class="space-y-5">
    <div class="px-4 py-3 bg-blue-50/70 border border-blue-200/80 rounded-xl text-[11px] text-blue-900 leading-relaxed">
      <span class="font-semibold">运行与路由：</span>
      基础功能、渠道与路由安全。实例 = 账号 + 渠道；同一账号的 PG/Build 共享一个 context，
      但算两个用量窗口。页面预算由实例数派生，不在这里配置。
    </div>

    <div class="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
      渠道
    </div>
    <ChannelRegistryCard
      :channels="features.channels"
      @update:channels="updateChannels"
    />

    <div class="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
      路由安全
    </div>
    <RoutingSafetyCard
      :routing="features.routing || {}"
      @update:routing="updateRouting"
    />

    <div class="px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-[11px] text-gray-500">
      自定义策略组与高级内存优化暂未开放，已作为占位功能隐藏；数据保留，不影响当前调度。
    </div>

    <div class="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
      高级协议
    </div>
    <AdvancedProtocolCard
      :features="features"
      @update:features="patch"
    />
  </div>
</template>
