<script setup lang="ts">
import { computed } from 'vue'
import {
  ArrowLeftRight,
  Circle,
  HelpCircle,
  Pencil,
  RefreshCw,
  RotateCcw,
  Trash2,
} from 'lucide-vue-next'
import type { AccountWithStats, Instance } from '@/types'
import Button from '@/components/ui/Button.vue'
import {
  channelLabel,
  instanceStateDotClass,
  instanceStateLabel,
  tierLabel,
  tierPillClass,
} from '@/lib/instances.ts'
import { formatDate } from '@/utils/format.ts'

const props = defineProps<{
  account: AccountWithStats
  instances: Instance[]
  ordinals?: Record<string, number>
  closingInstanceId?: string | null
  refreshing?: boolean
}>()

const emit = defineEmits<{
  close: [id: string]
  refresh: []
  edit: []
  remove: []
  clearCooldown: []
  switch: [id: string]
}>()

const label = computed(() => props.account.email || props.account.name || props.account.id)
const accountExpired = computed(
  () => Boolean(props.account.expired || props.account.session_expired),
)
const limited = computed(
  () => !accountExpired.value && props.account.is_available === false,
)
function closeDisabled(instance: Instance): boolean {
  return instance.state === 'starting' || instance.state === 'reclaiming'
}
const detailText = computed(() => {
  const parts = [
    `账号：${label.value}`,
    `ID：${props.account.id}`,
    `登录位：u/${props.account.auth_user || '0'}`,
  ]
  if (props.account.cookie_id) parts.push(`来源：${props.account.cookie_id}`)
  if (props.account.last_used) parts.push(`最近使用：${formatDate(props.account.last_used)}`)
  return parts.join('\n')
})
</script>

<template>
  <article class="px-4 py-4 sm:px-5 sm:py-5">
    <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
      <div class="flex items-start gap-3 min-w-0">
        <div class="shrink-0 w-9 h-9 rounded-xl border border-gray-200 bg-gray-50 flex items-center justify-center">
          <span class="text-[10px] font-mono font-bold text-gray-500">u/{{ account.auth_user || '0' }}</span>
        </div>
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <span
              class="text-sm font-semibold text-gray-900 truncate"
              :title="detailText"
            >
              {{ label }}
            </span>
            <span
              class="inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-semibold"
              :class="tierPillClass(account.tier)"
            >
              {{ tierLabel(account.tier) }}
            </span>
            <span
              v-if="account.duplicate_of"
              class="inline-flex items-center rounded-full border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700"
              title="同一邮箱存在多个账号；此为重复项，建议删除冗余账号"
            >
              重复
            </span>
            <span
              v-if="accountExpired"
              class="inline-flex items-center rounded-full border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700"
            >
              账号失效
            </span>
            <span
              v-else-if="limited"
              class="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700"
            >
              配额锁定
            </span>
            <span
              v-else
              class="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700"
            >
              可用
            </span>
          </div>
          <div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-400">
            <span
              v-if="account.cookie_id"
              class="font-mono truncate max-w-[18rem]"
              :title="account.cookie_id"
            >
              来源：{{ account.cookie_id }}
            </span>
            <span v-if="account.last_used">最近使用：{{ formatDate(account.last_used) }}</span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-1.5 shrink-0 self-end lg:self-start">
        <button
          v-if="limited"
          type="button"
          class="p-1.5 text-amber-600 hover:bg-amber-50 rounded-lg transition-colors cursor-pointer"
          title="清除该账号的配额锁定"
          @click="emit('clearCooldown')"
        >
          <RotateCcw class="w-3.5 h-3.5" />
        </button>
        <button
          type="button"
          class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer disabled:opacity-40"
          title="刷新账号档位与过期状态"
          :disabled="refreshing"
          @click="emit('refresh')"
        >
          <RefreshCw
            class="w-3.5 h-3.5"
            :class="{ 'animate-spin': refreshing }"
          />
        </button>
        <button
          type="button"
          class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          title="编辑账号名称"
          @click="emit('edit')"
        >
          <Pencil class="w-3.5 h-3.5" />
        </button>
        <button
          type="button"
          class="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
          title="删除账号"
          @click="emit('remove')"
        >
          <Trash2 class="w-3.5 h-3.5" />
        </button>
      </div>
    </div>

    <div
      v-if="instances.length"
      class="mt-3 space-y-2"
    >
      <div
        v-for="instance in instances"
        :key="instance.id"
        class="rounded-xl border border-gray-200/90 bg-gray-50/50 p-3.5"
      >
        <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <Circle
                class="w-2.5 h-2.5 fill-current"
                :class="instanceStateDotClass(instance.state)"
              />
              <span class="text-xs font-mono font-semibold text-gray-700">实例 #{{ ordinals?.[instance.id] ?? instance.number }}</span>
              <span
                class="rounded border border-gray-200 bg-white px-1.5 py-0.5 font-mono text-[10px] font-semibold text-gray-600"
              >
                {{ channelLabel(instance.channel) }}
              </span>
              <span class="text-[11px] text-gray-500">{{ instanceStateLabel(instance.state) }}</span>
              <span
                v-if="instance.latest"
                class="inline-flex items-center gap-0.5 rounded-full border border-brand-200 bg-brand-50 px-1.5 py-0.5 text-[10px] font-semibold text-brand-700"
              >
                最新实例
                <span
                  class="inline-flex"
                  title="最近启动的实例，仅用于展示，不代表路由优先级"
                >
                  <HelpCircle
                    class="w-3 h-3 text-brand-500"
                    aria-label="最近启动的实例，仅用于展示，不代表路由优先级"
                    role="img"
                  />
                </span>
              </span>
            </div>
            <div class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500">
              <span :class="instance.in_flight > 0 ? 'text-amber-700' : 'text-gray-400'">
                在途 {{ instance.in_flight }}
              </span>
            </div>
          </div>
          <div class="flex items-center gap-2 w-full sm:w-auto shrink-0">
            <Button
              variant="secondary"
              size="sm"
              class="flex-1 sm:flex-none"
              :disabled="instance.state !== 'active'"
              :title="instance.state === 'active' ? '打开实例切换面板' : '实例尚未就绪'"
              @click="emit('switch', instance.id)"
            >
              <ArrowLeftRight class="w-3.5 h-3.5" />
              切换
            </Button>
            <Button
              variant="outline"
              size="sm"
              class="flex-1 sm:flex-none"
              :loading="closingInstanceId === instance.id"
              :disabled="closeDisabled(instance)"
              :title="closeDisabled(instance)
                ? '实例正在启动或回收，暂不能关闭'
                : '关闭该实例并释放容量'"
              @click="emit('close', instance.id)"
            >
              关闭
            </Button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-else
      class="mt-3 rounded-xl border border-dashed border-gray-200 bg-gray-50/40 p-3.5"
    >
      <p class="text-xs text-gray-500">
        <span class="font-medium text-gray-700">{{ accountExpired ? '账号失效' : '未登录' }}</span>
        <span class="mx-1 text-gray-300">·</span>
        <span class="text-gray-400">{{ accountExpired ? '请重新导入凭据' : '未激活实例' }}</span>
      </p>
    </div>
  </article>
</template>
