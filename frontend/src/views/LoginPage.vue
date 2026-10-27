<script setup lang="ts">
// 登录：用户名 + 密码；登录后回到原来要去的页面
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/http'
import { LOCALES, type Locale, setLocale } from '@/i18n'
import { useUserStore } from '@/stores/user'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const user = useUserStore()
const form = reactive({ username: '', password: '', busy: false, error: '' })

async function submit(): Promise<void> {
  if (!form.username.trim() || !form.password) return
  form.busy = true
  form.error = ''
  try {
    await user.login(form.username.trim(), form.password)
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/') ? route.query.next : '/projects'
    await router.replace(next)
  } catch (err) {
    if (err instanceof ApiError && err.code === 'auth.locked') {
      form.error = t('login.locked', { n: Number(err.params.minutes ?? 5) })
    } else if (err instanceof ApiError && err.status === 401) {
      form.error = t('login.bad')
    } else {
      form.error = err instanceof ApiError && err.code === 'network' ? t('error.network') : String(err)
    }
  } finally {
    form.busy = false
  }
}
</script>

<template>
  <div class="login">
    <el-card class="card" shadow="always">
      <div class="brand">
        <img src="/favicon.svg" alt="" class="logo" />
        <span class="name">{{ t('app.name') }}</span>
        <span class="title">{{ t('app.title') }}</span>
      </div>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item :label="t('login.username')">
          <el-input v-model="form.username" autofocus autocomplete="username" data-test="login-username" />
        </el-form-item>
        <el-form-item :label="t('login.password')">
          <el-input v-model="form.password" type="password" show-password autocomplete="current-password" data-test="login-password" />
        </el-form-item>
        <el-alert v-if="form.error" type="error" :closable="false" :title="form.error" class="mb" data-test="login-error" />
        <el-button type="primary" native-type="submit" :loading="form.busy" class="full" data-test="login-submit">
          {{ t('login.submit') }}
        </el-button>
      </el-form>
      <div class="foot">
        <span class="muted">{{ t('login.forgot') }}</span>
        <el-select :model-value="locale" size="small" class="lang" @change="(v: Locale) => setLocale(v)">
          <el-option v-for="l in LOCALES" :key="l" :value="l" :label="t(`lang.${l}`)" />
        </el-select>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--brand-navy);
}
.card {
  width: 380px;
}
.brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 20px;
}
.logo {
  width: 24px;
  height: 24px;
  align-self: center;
}
.name {
  font-weight: 700;
  font-size: 20px;
}
.title {
  color: var(--el-text-color-secondary);
}
.full {
  width: 100%;
}
.mb {
  margin-bottom: 12px;
}
.foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 14px;
}
.lang {
  width: 110px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
