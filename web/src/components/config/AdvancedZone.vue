<script setup lang="ts">
import { computed } from 'vue'
import type {
  AccountGroupItem,
  ChannelsConfig,
  FeaturesConfig,
  RoutingConfig,
} from './types.ts'
import ChannelRegistryCard from './ChannelRegistryCard.vue'
import FeatureGroupsEditor from './FeatureGroupsEditor.vue'
import RoutingSafetyCard from './RoutingSafetyCard.vue'
import MemoryOptimizationCard from './MemoryOptimizationCard.vue'
import AdvancedProtocolCard from './AdvancedProtocolCard.vue'
import { Info } from 'lucide-vue-next'

const props = defineProps<{
  features: FeaturesConfig
}>()

const emit = defineEmits<{
  'update:features': [val: FeaturesConfig]
}>()

const singleAccount = computed(
  () => Math.max(1, Number(props.features.routing?.max_contexts ?? 1) || 1) <= 1,
)

function patch(patchValue: Partial<FeaturesConfig>) {
  emit('update:features', { ...props.features, ...patchValue })
}

function updateChannels(val: ChannelsConfig) {
  patch({ channels: val })
}

function updateGroups(val: AccountGroupItem[]) {
  patch({ groups: val })
}

function updateRouting(val: RoutingConfig) {
  patch({ routing: val })
}
</script>

<template>
  <div class="space-y-5">
    <div class="px-4 py-3 bg-blue-50/70 border border-blue-200/80 rounded-xl text-[11px] text-blue-900 leading-relaxed">
      <span class="font-semibold">深度模式：</span>
      修改会直接影响账号容量、渠道与路由。账号是登录会话；实例是账号+渠道的独立用量窗口，
      同一账号的 PG/Build 共享一个上下文。页面预算只在高级内存优化里配置。
      所有修改统一通过「保存并热重载」写入。
    </div>

    <ChannelRegistryCard
      :channels="features.channels"
      :build-surface="Boolean(features.build_surface)"
      @update:channels="updateChannels"
    />

    <RoutingSafetyCard
      :routing="features.routing || {}"
      @update:routing="updateRouting"
    />

    <div
      v-if="singleAccount"
      class="flex items-start gap-2 px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-[11px] text-gray-500"
    >
      <Info class="w-4 h-4 text-gray-400 shrink-0 mt-0.5" />
      <p>
        单账号模式：自定义策略组与高级内存优化已收起。该账号仍允许 PG+Build 两个用量窗口（1c2p）；
        把账号会话上限调到 2 以上并保存后会显示这些高级项。
      </p>
    </div>

    <template v-else>
      <FeatureGroupsEditor
        :groups="features.groups || []"
        collapsible
        @update:groups="updateGroups"
      />

      <MemoryOptimizationCard
        :routing="features.routing || {}"
        :channels="features.channels || {}"
        :groups="features.groups || []"
        :max-contexts="Math.max(2, Math.max(1, Number(features.routing?.max_contexts ?? 1) || 1))"
        @update:routing="updateRouting"
        @update:channels="updateChannels"
        @update:groups="updateGroups"
      />
    </template>

    <AdvancedProtocolCard
      :features="features"
      @update:features="patch"
    />
  </div>
</template>
