<script setup lang="ts">
// 导入预览里的错误 / 提醒列表
import { useI18n } from 'vue-i18n'

import type { Issue } from '@/api/structure'

defineProps<{ errors?: Issue[]; warnings?: Issue[] }>()
const { t } = useI18n()
</script>

<template>
  <div class="issues">
    <el-alert
      v-for="(e, i) in errors ?? []"
      :key="`e${i}`"
      type="error"
      :closable="false"
      show-icon
      :title="e.row ? t('import.rowPrefix', { row: e.row }) + e.message : e.message"
    />
    <el-alert
      v-for="(w, i) in warnings ?? []"
      :key="`w${i}`"
      type="warning"
      :closable="false"
      show-icon
      :title="w.row ? t('import.rowPrefix', { row: w.row }) + w.message : w.message"
    />
  </div>
</template>

<style scoped>
.issues {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 8px 0;
}
</style>
