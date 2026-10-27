// 检查组件和脚本里有没有直接写中文（界面文字必须放进 src/i18n 的语言文件）。
// 注释里的中文不算；src/i18n 目录不检查。用法：node scripts/check-i18n-text.mjs
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const SRC = join(ROOT, 'src')
const SKIP = [join(SRC, 'i18n')]
const CJK = /[\u3400-\u9fff\uff00-\uffef\u3000-\u303f]/

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (SKIP.some((s) => path.startsWith(s))) return []
    if (statSync(path).isDirectory()) return files(path)
    return /\.(vue|ts)$/.test(name) && !name.endsWith('.d.ts') ? [path] : []
  })
}

// 去掉注释，保留换行（行号不变）
function stripComments(text) {
  const blank = (m) => m.replace(/[^\n]/g, ' ')
  return text
    .replace(/<!--[\s\S]*?-->/g, blank)
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/(^|[^:'"`])\/\/.*$/gm, (m, p) => p + ' '.repeat(m.length - p.length))
}

const problems = []
for (const file of files(SRC)) {
  stripComments(readFileSync(file, 'utf8'))
    .split('\n')
    .forEach((line, i) => {
      if (CJK.test(line)) problems.push(`${relative(ROOT, file)}:${i + 1}: ${line.trim()}`)
    })
}
if (problems.length) {
  console.error('界面文字要写进 src/i18n 的语言文件，下面这些地方直接写了中文：')
  for (const p of problems) console.error('  ' + p)
  process.exit(1)
}
console.log('界面文字检查通过：组件和脚本里没有直接写中文')
