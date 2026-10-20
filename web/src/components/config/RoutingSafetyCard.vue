<script setup lang="ts">
import type { CircuitBreakerConfig, RoutingConfig } from './types.ts'
import ConfigInput from './ConfigInput.vue'
import ConfigSwitch from './ConfigSwitch.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import { Activity, ShieldCheck } from 'lucide-vue-next'

const props = withDefaults(defineProps<{ routing?: RoutingConfig }>(), {
  routing: () => ({}),
})

const emit = defineEmits<{
  'update:routing': [val: RoutingConfig]
}>()

function update(key: keyof RoutingConfig, value: number | boolean) {
  emit('update:routing', { ...props.routing, [key]: value })
}

function updateBreaker(key: keyof CircuitBreakerConfig, value: number) {
  const breaker = { ...(props.routing.circuit_breaker || {}), [key]: value }
  emit('update:routing', { ...props.routing, circuit_breaker: breaker })
}

function toNumber(val: string | number | null, fallback: number): number {
  const n = Number(val)
  return Number.isFinite(n) ? n : fallback
}

function nonNegative(val: string | number | null, fallback: number): number {
  return Math.max(0, Math.round(toNumber(val, fallback)))
}

const breaker = () => props.routing.circuit_breaker || {}
</script>

<template>
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center gap-2.5">
      <ShieldCheck class="w-4 h-4 text-brand-600" />
      <div>
        <h4 class="font-bold text-gray-900 text-xs flex items-center gap-1.5">
          <span>路由安全</span>
          <FieldHelpTip
            title="路由安全"
            content="控制失败换号次数、额度借用与熔断；不改变实例身份。"
            explanation="排序：可用 → 亲和度 → 组权重 → 最久未用 → 请求数 → 编号。"
          />
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          组权重、公平性与熔断在这里；实例创建/关闭/换账号不在这里。
        </p>
      </div>
    </div>

    <div class="p-5 space-y-4">
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div class="flex items-end gap-1.5">
          <div class="flex-1">
            <ConfigInput
              :model-value="routing.max_switches ?? 2"
              type="number"
              label="单请求换号上限"
              description="max_switches"
              placeholder="2"
              @update:model-value="update('max_switches', nonNegative($event, 2))"
            />
          </div>
          <FieldHelpTip
            schema-key="features.routing.max_switches"
            class="mb-2 shrink-0"
          />
        </div>

        <div class="flex items-center gap-1.5">
          <div class="flex-1">
            <ConfigSwitch
              :model-value="Boolean(routing.borrow)"
              label="临时借入就绪实例"
              description="仅首字节前；默认关闭"
              @update:model-value="update('borrow', $event)"
            />
          </div>
          <FieldHelpTip
            schema-key="features.routing.borrow"
            class="shrink-0"
          />
        </div>
      </div>

      <div class="rounded-xl border border-gray-200/80 bg-gray-50/60 px-3.5 py-3 text-[11px] text-gray-500 leading-relaxed">
        排序固定为：可用 → 渠道亲和度 → 自定义策略组权重 → 最久未用 → 请求数 → 实例编号。
        没有“当前实例/优先实例”排序项；最新实例只是最近启动的展示。
      </div>

      <div class="p-4 bg-gray-50/80 border border-gray-200/80 rounded-xl space-y-3">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <Activity class="w-4 h-4 text-rose-500" />
          <span>路由熔断</span>
          <FieldHelpTip schema-key="features.routing.circuit_breaker" />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <ConfigInput
            :model-value="breaker().window_seconds ?? 600"
            type="number"
            label="统计窗口（秒）"
            description="window_seconds"
            placeholder="600"
            @update:model-value="updateBreaker('window_seconds', nonNegative($event, 600))"
          />
          <ConfigInput
            :model-value="breaker().failure_threshold ?? 5"
            type="number"
            label="失败阈值"
            description="failure_threshold"
            placeholder="5"
            @update:model-value="updateBreaker('failure_threshold', nonNegative($event, 5))"
          />
          <ConfigInput
            :model-value="breaker().cooldown_seconds ?? 300"
            type="number"
            label="冷却时长（秒）"
            description="cooldown_seconds"
            placeholder="300"
            @update:model-value="updateBreaker('cooldown_seconds', nonNegative($event, 300))"
          />
        </div>
        <p class="text-[11px] text-gray-400">
          滚动窗口内失败切换达到阈值 → 全局冷却；冷却期间请求显式 503，不会继续放大失败。
        </p>
      </div>
    </div>
  </div>
</template>
