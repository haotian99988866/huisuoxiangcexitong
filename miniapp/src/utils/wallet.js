import { API_BASE_URL } from './twa'

export async function requestWallet(action, fields = {}) {
  const initData = window.Telegram?.WebApp?.initData
  if (!initData) throw new Error('请从 Telegram 打开小程序，以确认你的账户身份。')
  const response = await fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, init_data: initData, ...fields }),
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '账户请求失败，请稍后重试')
  return result
}
