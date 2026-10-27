<script setup lang="ts">
// 从 Excel 粘贴多行：粘贴 → 预览（第一行像表头就按表头对列）→ 确认
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { mapRows, parseTsv, type PasteColumn } from '@/utils/paste'

const props = defineProps<{
  title: string
  columns: (PasteColumn & { label: string })[]
  hint?: string
  loading?: boolean
}>()
const emit = defineEmits<{ confirm: [rows: Record<string, string | null>[]] }>()
const open = defineModel<boolean>({ required: true })
const { t } = useI18n()

const text = ref('')
watch(open, (v) => v && (text.value = ''))

const parsed = computed(() => mapRows(parseTsv(text.value), props.columns))
</script>

<template>
  <el-dialog v-model="open" :title="title" width="860px" top="6vh">
    <p class="hint">{{ hint || t('paste.hint') }}</p>
    <p class="hint">{{ t('paste.columns') }}{{ columns.map((c) => c.label).join(' | ') }}</p>
    <el-input v-model="text" type="textarea" :rows="6" :placeholder="t('paste.placeholder')" class="paste-box" />
    <template v-if="parsed.records.length">
      <p class="hint">
        {{ t('paste.parsed', { n: parsed.records.length }) }}
        <span v-if="parsed.header">{{ t('paste.headerDetected') }}</span>
      </p>
      <el-table :data="parsed.records" max-height="300" size="small" border>
        <el-table-column type="index" width="50" />
        <el-table-column v-for="c in columns" :key="c.key" :prop="c.key" :label="c.label" min-width="120" />
      </el-table>
    </template>
    <template #footer>
      <el-button @click="open = false">{{ t('common.cancel') }}</el-button>
      <el-button
        type="primary"
        :disabled="!parsed.records.length"
        :loading="loading"
        @click="emit('confirm', parsed.records)"
      >
        {{ t('paste.next', { n: parsed.records.length }) }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hint {
  color: var(--el-text-color-secondary);
  margin: 0 0 8px;
}
.paste-box {
  margin-bottom: 12px;
}
</style>
