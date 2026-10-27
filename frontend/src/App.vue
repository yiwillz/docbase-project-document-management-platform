<script setup lang="ts">
import { elementLocale } from '@/i18n'
</script>

<template>
  <el-config-provider :locale="elementLocale">
    <router-view />
  </el-config-provider>
</template>
