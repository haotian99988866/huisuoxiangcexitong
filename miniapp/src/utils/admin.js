import { API_BASE_URL } from './twa'
import { isTenantEntry } from './tenant'

export async function requestAdmin(action, fields = {}) {
  if (isTenantEntry()) throw new Error('请从平台主入口进入管理台')
  const initData = window.Telegram?.WebApp?.initData
  if (!initData) throw new Error('请从 Telegram 打开管理台，以验证管理员身份')
  const response = await fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...fields, action, init_data: initData }),
  })
  const result = await response.json()
  if (!response.ok) {
    const error = new Error(result.error || '管理请求失败，请稍后重试')
    error.status = response.status
    throw error
  }
  return result
}

export function localDateInput(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  const pad = value => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function utcExpiry(local) {
  if (!local) return null
  const date = new Date(local)
  if (Number.isNaN(date.getTime())) throw new Error('请选择有效到期时间')
  return date.toISOString().replace(/\.\d{3}Z$/, 'Z')
}

export function tenantLink(slug) {
  return `https://t.me/haotianzhaopian_bot?startapp=tenant_${encodeURIComponent(slug)}`
}
