import { API_BASE_URL } from './twa'
import { tenantHeaders } from './tenant'

export async function requestCustomerDuty(action, fields) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 15000)
  try {
    const response = await fetch(`${API_BASE_URL}/webhook`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
      body: JSON.stringify({ ...fields, action, init_data: window.Telegram?.WebApp?.initData }),
      cache: 'no-store', signal: controller.signal,
    })
    const result = await response.json()
    if (!response.ok) {
      const error = new Error(result.error || '出勤订阅请求失败')
      error.status = response.status
      throw error
    }
    return result
  } finally { clearTimeout(timeout) }
}
