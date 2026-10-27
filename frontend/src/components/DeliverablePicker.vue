<script setup lang="ts">
// 选文件放到哪：识别出的候选排在前面（带置信度），下面是全部应交项（可搜），还可以选"待归类"
// 值是应交项 id 用逗号连起来的字符串（几台设备一个文件："12,13"），空字符串 = 待归类
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { Item } from '@/api/deliverables'
import type { Located } from '@/api/files'

const props = defineProps<{ items: Item[]; candidates?: Located[]; allowUnclassified?: boolean }>()
const model = defineModel<string>({ required: true })
const { t } = useI18n()

const byId = computed(() => new Map(props.items.map((i) => [i.id, i])))

function where(i: Item): string {
  return [i.equipment_tag, i.equipment_name].filter(Boolean).join(' ') || i.module_name || t('matrix.wholeType')
}

function label(ids: number[]): string {
  const found = ids.map((id) => byId.value.get(id)).filter((i): i is Item => !!i)
  if (!found.length) return ids.join(', ')
  const first = found[0]!
  return `${first.submittal_no} ${first.document_type_name} / ${found.map(where).join(' & ')}`
}

const candidateOptions = computed(() =>
  (props.candidates ?? []).map((c) => ({
    value: c.deliverable_ids.join(','),
    label: label(c.deliverable_ids),
    level: c.level,
  })),
)
const candidateKeys = computed(() => new Set(candidateOptions.value.map((o) => o.value)))
const allOptions = computed(() =>
  props.items.filter((i) => !candidateKeys.value.has(String(i.id))).map((i) => ({ value: String(i.id), label: label([i.id]) })),
)
</script>

<template>
  <el-select v-model="model" filterable :placeholder="t('drop.pickTarget')" class="picker">
    <el-option v-if="allowUnclassified" value="" :label="t('drop.unclassified')" />
    <el-option-group v-if="candidateOptions.length" :label="t('drop.candidates')">
      <el-option v-for="o in candidateOptions" :key="'c' + o.value" :value="o.value" :label="o.label">
        <span>{{ o.label }}</span>
        <el-tag size="small" :type="o.level === 'high' ? 'success' : o.level === 'medium' ? 'warning' : 'info'" class="lvl">
          {{ t(`drop.level.${o.level}`) }}
        </el-tag>
      </el-option>
    </el-option-group>
    <el-option-group :label="t('drop.allDeliverables')">
      <el-option v-for="o in allOptions" :key="o.value" :value="o.value" :label="o.label" />
    </el-option-group>
  </el-select>
</template>

<style scoped>
.picker {
  width: 100%;
}
.lvl {
  margin-left: 8px;
}
</style>
