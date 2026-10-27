// 登录、当前用户、改密码；用户管理（系统管理员）；角色；项目成员
import { api } from './http'

export interface Me {
  id: number
  username: string
  display_name: string | null
  email: string | null
  is_sysadmin: boolean
  active: boolean
  projects: Record<string, string[]> // 项目 id → 权限码（系统管理员不列，按全部处理）
  all_permissions: string[]
}

export interface UserRow {
  id: number
  username: string
  display_name: string | null
  email: string | null
  is_sysadmin: boolean
  active: boolean
  auth_provider: string
  last_login_at: string | null
  locked: boolean
  created_at: string
}

export interface Role {
  code: string
  name: string
  permissions: string[]
  builtin: boolean
}

export interface Member {
  user_id: number
  username: string
  display_name: string | null
  active: boolean
  role: string
  role_name: string
}

export const authApi = {
  login: (username: string, password: string) => api.post<Me>('/api/auth/login', { username, password }),
  logout: () => api.post<{ ok: boolean }>('/api/auth/logout'),
  me: () => api.get<Me>('/api/auth/me'),
  changePassword: (oldPassword: string, newPassword: string) =>
    api.post<{ ok: boolean }>('/api/auth/password', { old_password: oldPassword, new_password: newPassword }),
  users: () => api.get<UserRow[]>('/api/users'),
  createUser: (data: { username: string; password: string; display_name?: string; email?: string; is_sysadmin?: boolean }) =>
    api.post<UserRow>('/api/users', data),
  updateUser: (id: number, data: Partial<Pick<UserRow, 'display_name' | 'email' | 'is_sysadmin' | 'active'>>) =>
    api.patch<UserRow>(`/api/users/${id}`, data),
  resetPassword: (id: number, password: string) => api.post<UserRow>(`/api/users/${id}/password`, { password }),
  roles: () => api.get<Role[]>('/api/roles'),
  members: (pid: number) => api.get<Member[]>(`/api/projects/${pid}/members`),
  memberCandidates: (pid: number) =>
    api.get<{ id: number; username: string; display_name: string | null }[]>(`/api/projects/${pid}/members/candidates`),
  setMember: (pid: number, userId: number, role: string) =>
    api.put<Member[]>(`/api/projects/${pid}/members/${userId}`, { role }),
  removeMember: (pid: number, userId: number) => api.delete<Member[]>(`/api/projects/${pid}/members/${userId}`),
}
