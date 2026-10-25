import { API_BASE_URL } from './twa'
import { isTenantEntry } from './tenant'

export async function requestMusic(action, fields = {}) {
  if (isTenantEntry()) throw new Error('此入口不开放音乐')
  const initData = window.Telegram?.WebApp?.initData
  if (action !== 'music_shared' && !initData) throw new Error('请从 Telegram 打开小程序，以保存你的音乐数据')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15000)
  try {
    const response = await fetch(`${API_BASE_URL}/webhook`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...fields, action, init_data: initData }), signal: controller.signal, keepalive: true,
    })
    const result = await response.json()
    if (!response.ok) throw new Error(result.error || '音乐账户请求失败')
    return result
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('音乐账户请求超时，请重试')
    throw error
  } finally { clearTimeout(timer) }
}
