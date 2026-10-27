<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>

<template>
  <el-result icon="warning" :title="t('error.notFound')">
    <template #extra>
      <router-link to="/projects">
        <el-button type="primary">{{ t('error.backHome') }}</el-button>
      </router-link>
    </template>
  </el-result>
</template>
