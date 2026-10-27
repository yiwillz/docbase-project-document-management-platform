// 项目列表和当前项目（顶栏切换用）
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { type Project, projectsApi } from '@/api/projects'

export const useProjectStore = defineStore('projects', () => {
  const projects = ref<Project[]>([])
  const currentId = ref<number | null>(null)
  const loaded = ref(false)

  const current = computed(() => projects.value.find((p) => p.id === currentId.value) ?? null)

  async function load(): Promise<void> {
    projects.value = await projectsApi.list(true)
    loaded.value = true
  }

  function upsert(project: Project): void {
    const i = projects.value.findIndex((p) => p.id === project.id)
    if (i >= 0) projects.value.splice(i, 1, project)
    else projects.value.push(project)
  }

  return { projects, currentId, current, loaded, load, upsert }
})
