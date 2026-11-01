import { API_BASE_URL } from './twa'
import { isTenantEntry, tenantHeaders } from './tenant'

export async function requestHotels(page, signal) {
  const response = await fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify({ action: 'get_hotels', page }), cache: 'no-store', signal,
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '酒店推荐读取失败')
  return result
}

export async function saveHotel(payload, image) {
  if (isTenantEntry()) throw new Error('请从主入口管理酒店')
  const initData = window.Telegram?.WebApp?.initData
  if (!initData) throw new Error('请从 Telegram 打开，以验证管理员身份')
  const form = new FormData()
  form.set('payload', JSON.stringify({ ...payload, init_data: initData }))
  if (image) form.set('image', image)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 30000)
  try {
    const response = await fetch(`${API_BASE_URL}/admin-hotel-save`, {
      method: 'POST', body: form, cache: 'no-store', signal: controller.signal,
    })
    if (response.status === 413) {
      const failure = new Error('图片过大，请选择不超过5MB的照片')
      failure.status = 413
      throw failure
    }
    const result = await response.json()
    if (!response.ok) {
      const failure = new Error(result.error || '酒店保存失败')
      failure.status = response.status
      throw failure
    }
    return result
  } catch (failure) {
    if (failure.name === 'AbortError') throw new Error('酒店保存结果尚未确认，请使用原操作编号重试')
    throw failure
  } finally { clearTimeout(timer) }
}
