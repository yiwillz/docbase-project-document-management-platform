<script setup lang="ts">
// 命名模板（按项目配置）：说明每个占位符和每一行用在哪；改模板时当场检查写法对不对；空着用默认
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { showError } from '@/api/errors'
import { filesApi } from '@/api/files'
import { type Project, projectsApi } from '@/api/projects'

const props = defineProps<{ projectId: number; readonly: boolean }>()
const emit = defineEmits<{ saved: [project: Project] }>()
const { t } = useI18n()

const TEXT_KEYS = [
  'equipment',
  'equipment_untagged',
  'equipment_titled',
  'equipment_untagged_titled',
  'general',
  'unclassified_prefix',
  'tag_joiner',
] as const
const DATE_FORMATS = [
  { value: '%Y%m%d', key: 'compact' },
  { value: '%Y-%m-%d', key: 'dashed' },
  { value: '%y%m%d', key: 'short' },
  { value: '%d%m%Y', key: 'dayFirst' },
]
// 占位符说明的顺序；常见改法（只做说明）
const PLACEHOLDERS = ['no', 'tags', 'type', 'equipment', 'title', 'date', 'version'] as const
const RECIPES = [
  { key: 'dateFirst', template: '{date}_{no}.{tags}_{type}-{equipment}-V{version}' },
  { key: 'noEquipment', template: '{no}.{tags}_{type}-{date}-V{version}' },
  { key: 'revLetter', template: '{no}.{tags}_{type}-{equipment}-{date}-Rev{version}' },
] as const
const showHelp = ref(true)
const placeholderRows = computed(() =>
  PLACEHOLDERS.filter((key) => !fields.value.length || fields.value.includes(key)).map((key) => ({ key })),
)

const defaults = ref<Record<string, string | number>>({})
const fields = ref<string[]>([])
const form = reactive<Record<string, string | number | null>>({})
const error = ref<string | null>(null)
const reason = ref('')
const saving = ref(false)

function current(): Record<string, string | number> {
  const out: Record<string, string | number> = {}
  for (const [k, v] of Object.entries(form)) if (v !== null && v !== '') out[k] = v
  return out
}

async function load(): Promise<void> {
  try {
    const cfg = await filesApi.naming(props.projectId)
    defaults.value = cfg.defaults
    fields.value = cfg.fields
    for (const k of [...TEXT_KEYS, 'date_format', 'max_length']) form[k] = cfg.templates[k] ?? null
    error.value = null
  } catch (err) {
    showError(err)
  }
}

let timer: ReturnType<typeof setTimeout> | undefined
watch(form, () => {
  clearTimeout(timer)
  timer = setTimeout(async () => {
    try {
      // 只用来检查写法（如少了 {version}、占位符写错）；示例不再显示
      await filesApi.namingPreview(props.projectId, current())
      error.value = null
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    }
  }, 300)
})

async function save(): Promise<void> {
  saving.value = true
  try {
    const project = await projectsApi.update(props.projectId, {
      naming_templates: current(),
      reason: reason.value.trim() || undefined,
    })
    reason.value = ''
    ElMessage.success(t('common.saved'))
    emit('saved', project)
  } catch (err) {
    showError(err)
  } finally {
    saving.value = false
  }
}

function reset(): void {
  for (const k of Object.keys(form)) form[k] = null
}

watch(() => props.projectId, load)
onMounted(load)
</script>

<template>
  <div>
    <p class="muted">{{ t('naming.intro') }}</p>

    <!-- 怎么填 -->
    <el-collapse :model-value="showHelp ? ['help'] : []" class="help" @change="(v: string | number | (string | number)[]) => (showHelp = Array.isArray(v) && v.includes('help'))">
      <el-collapse-item name="help" :title="t('naming.help.title')" data-test="naming-help">
        <p>{{ t('naming.help.how') }}</p>
        <el-table :data="placeholderRows" size="small" border class="mb">
          <el-table-column :label="t('naming.help.placeholder')" width="130">
            <template #default="{ row }"><code>{{ '{' + row.key + '}' }}</code></template>
          </el-table-column>
          <el-table-column :label="t('naming.help.meaning')">
            <template #default="{ row }">{{ t(`naming.ph.${row.key}`) }}</template>
          </el-table-column>
          <el-table-column :label="t('naming.help.example')" width="200">
            <template #default="{ row }"><code>{{ t(`naming.phExample.${row.key}`) }}</code></template>
          </el-table-column>
        </el-table>
        <ul class="rules">
          <li v-for="n in 4" :key="n">{{ t(`naming.help.rule${n}`) }}</li>
        </ul>
        <p>{{ t('naming.help.recipes') }}</p>
        <ul class="rules">
          <li v-for="r in RECIPES" :key="r.key">
            {{ t(`naming.recipe.${r.key}`) }}: <code>{{ r.template }}</code>
          </li>
        </ul>
      </el-collapse-item>
    </el-collapse>

    <el-form label-width="260px" label-position="right" :disabled="readonly" class="form">
      <el-form-item v-for="k in TEXT_KEYS" :key="k" :label="t(`naming.key.${k}`)">
        <el-input v-model="form[k]" :placeholder="String(defaults[k] ?? '')" clearable :data-test="`naming-${k}`" />
        <div class="use">{{ t(`naming.use.${k}`) }}</div>
      </el-form-item>
      <el-form-item :label="t('naming.key.date_format')">
        <el-select v-model="form.date_format" clearable :placeholder="t(`naming.dateFormats.${DATE_FORMATS.find((f) => f.value === defaults.date_format)?.key ?? 'compact'}`)" class="w200">
          <el-option v-for="f in DATE_FORMATS" :key="f.value" :value="f.value" :label="t(`naming.dateFormats.${f.key}`)" />
        </el-select>
        <div class="use">{{ t('naming.use.date_format') }}</div>
      </el-form-item>
      <el-form-item :label="t('naming.key.max_length')">
        <el-input-number
          :model-value="(form.max_length as number | null) ?? undefined"
          :min="40"
          :max="200"
          :placeholder="String(defaults.max_length ?? '')"
          @update:model-value="(v: number | undefined | null) => (form.max_length = v ?? null)"
        />
        <div class="use">{{ t('naming.use.max_length') }}</div>
      </el-form-item>
      <el-form-item :label="t('common.reason')"><el-input v-model="reason" /></el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" :disabled="!!error" data-test="naming-save" @click="save">{{ t('common.save') }}</el-button>
        <el-button @click="reset">{{ t('naming.reset') }}</el-button>
      </el-form-item>
    </el-form>

    <el-alert v-if="error" type="error" :closable="false" :title="error" data-test="naming-error" />
  </div>
</template>

<style scoped>
.form {
  max-width: 820px;
}
.help {
  max-width: 820px;
  margin-bottom: 16px;
}
.rules {
  margin: 4px 0 10px;
  padding-left: 20px;
  line-height: 1.8;
}
.mb {
  margin-bottom: 8px;
}
.use {
  width: 100%;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
  margin-top: 2px;
}
/* 标签不折行 */
.form :deep(.el-form-item__label) {
  white-space: nowrap;
}
.w200 {
  width: 200px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
