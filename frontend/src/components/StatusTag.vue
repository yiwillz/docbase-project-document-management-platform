<script setup lang="ts">
// 状态标签：颜色和导出文件一致；逾期显示成红色"逾期"
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { Status } from '@/api/settings'
import { COLORS, displayKey } from '@/utils/status'

const props = defineProps<{ status: Status; overdue?: boolean }>()
const { t } = useI18n()
const key = computed(() => displayKey(props.status, !!props.overdue))
</script>

<template>
  <span
    class="status-tag"
    :style="{ background: COLORS[key].bg, color: COLORS[key].fg }"
    :data-status="key"
  >
    {{ key === 'overdue' ? t('status.overdue') : t(`status.${key}`) }}
  </span>
</template>

<style scoped>
.status-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
  white-space: nowrap;
  border: 1px solid rgba(0, 0, 0, 0.06);
}
</style>
