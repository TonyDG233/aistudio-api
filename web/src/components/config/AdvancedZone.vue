<script setup lang="ts">
import type {
  AccountGroupItem,
  ChannelsConfig,
  FeaturesConfig,
  RoutingConfig,
  SlotConfigItem,
} from './types.ts'
import ChannelRegistryCard from './ChannelRegistryCard.vue'
import FeatureGroupsEditor from './FeatureGroupsEditor.vue'
import SlotTableCard from './SlotTableCard.vue'
import RoutingSafetyCard from './RoutingSafetyCard.vue'
import OrchestrationSummaryCard from './OrchestrationSummaryCard.vue'
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

function updateGroups(val: AccountGroupItem[]) {
  patch({ groups: val })
}

function updateSlots(val: SlotConfigItem[]) {
  patch({ slots: val })
}

function updateRouting(val: RoutingConfig) {
  patch({ routing: val })
}
</script>

<template>
  <div class="space-y-5">
    <div class="px-4 py-3 bg-blue-50/70 border border-blue-200/80 rounded-xl text-[11px] text-blue-900 leading-relaxed">
      <span class="font-semibold">深度模式：</span>
      修改会直接影响路由、槽位和实例预算。max_pages 是页面上限，不是并发数；所有修改统一通过「保存并热重载」写入。
    </div>

    <ChannelRegistryCard
      :channels="features.channels"
      :build-surface="Boolean(features.build_surface)"
      @update:channels="updateChannels"
    />

    <FeatureGroupsEditor
      :groups="features.groups || []"
      @update:groups="updateGroups"
    />

    <SlotTableCard
      :slots="features.slots || []"
      :max-contexts="features.routing?.max_contexts ?? 2"
      @update:slots="updateSlots"
    />

    <RoutingSafetyCard
      :routing="features.routing || {}"
      @update:routing="updateRouting"
    />

    <OrchestrationSummaryCard
      :groups="features.groups || []"
      :slots="features.slots || []"
      :routing="features.routing || {}"
    />

    <AdvancedProtocolCard
      :features="features"
      @update:features="patch"
    />
  </div>
</template>