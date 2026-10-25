import { API_BASE_URL } from './twa'
import { tenantHeaders } from './tenant'

export async function requestAnnouncements(action, fields = {}, signal) {
  const response = await fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify({ action, ...fields }), signal,
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '公告加载失败')
  return result
}
