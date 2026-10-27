// 多语言：默认中文；英文缺的键回退中文。选择的语言存在浏览器里。
import { computed } from 'vue'
import { createI18n } from 'vue-i18n'
import elementEn from 'element-plus/es/locale/lang/en'
import elementZh from 'element-plus/es/locale/lang/zh-cn'

import en from './en'
import zhCN from './zh-CN'

export type Locale = 'zh-CN' | 'en'
export const LOCALES: Locale[] = ['zh-CN', 'en']
const STORAGE_KEY = 'docbase.locale'

function savedLocale(): Locale {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return LOCALES.includes(value as Locale) ? (value as Locale) : 'zh-CN'
  } catch {
    return 'zh-CN'
  }
}

export const i18n = createI18n({
  legacy: false,
  locale: savedLocale(),
  fallbackLocale: 'zh-CN',
  missingWarn: false,
  fallbackWarn: false,
  messages: { 'zh-CN': zhCN, en },
  datetimeFormats: {
    'zh-CN': {
      short: { year: 'numeric', month: '2-digit', day: '2-digit' },
      long: { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' },
    },
    en: {
      short: { year: 'numeric', month: '2-digit', day: '2-digit' },
      long: { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' },
    },
  },
})

export function setLocale(locale: Locale): void {
  i18n.global.locale.value = locale
  document.documentElement.lang = locale
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // 浏览器不让存就算了，只影响下次打开时的默认语言
  }
}

// Element Plus 自带文字（分页、日期选择等）跟着切换
export const elementLocale = computed(() => (i18n.global.locale.value === 'en' ? elementEn : elementZh))
