<script setup lang="ts">
// 还没做的页面：显示在哪一步做
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

defineProps<{ step: number }>()
const { t } = useI18n()
const route = useRoute()
</script>

<template>
  <el-card shadow="never">
    <el-result icon="info" :title="t(`menu.${String(route.name)}`)" :sub-title="t('placeholder.body', { step: `#${step}` })">
      <template #extra>
        <el-tag type="info">{{ t('placeholder.title') }}</el-tag>
      </template>
    </el-result>
  </el-card>
</template>
