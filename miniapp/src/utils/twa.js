import { contactUrl, tenantHeaders } from './tenant'

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')

export { bindBackButton, registerBackOverlay } from './backButton'

// t.me 链接交给 Telegram 内部打开(频道原帖);浏览器里退回新窗口
export function openTelegramLink(url) {
  window.dispatchEvent(new CustomEvent('customer-contact-open', { detail: url }))
  const tg = window.Telegram?.WebApp
  if (tg?.openTelegramLink) {
    const target = new URL(url)
    const contact = new URL(contactUrl())
    const isContact = target.origin === contact.origin && target.pathname.toLowerCase() === contact.pathname.toLowerCase()
    if (tg.platform === 'ios' && isContact && tg.openLink) {
      tg.openTelegramLink(url)
      return 'contact-direct'
    }
    tg.openTelegramLink(url)
  }
  else window.open(url, '_blank', 'noopener')
}

// 在 Telegram 内由顶部返回箭头负责退出详情，页面内不再重复放关闭按钮
export const hasTelegramBackButton = Boolean(window.Telegram?.WebApp?.initData && window.Telegram?.WebApp?.BackButton)

// 全屏时 Telegram 的关闭/菜单按钮会浮在页面顶部，需要读取是否全屏来做避让
export function subscribeFullscreen(callback) {
  const tg = window.Telegram?.WebApp
  if (!tg || !tg.onEvent) {
    callback(false)
    return () => {}
  }
  const update = () => callback(Boolean(tg.isFullscreen))
  update()
  tg.onEvent('fullscreenChanged', update)
  tg.onEvent('fullscreenFailed', update)
  return () => {
    tg.offEvent('fullscreenChanged', update)
    tg.offEvent('fullscreenFailed', update)
  }
}

/** 优先展示后端中文 message，兼容旧字段 error。 */
export function errorText(error, fallback = '服务暂时不可用，请稍后重试。') {
  if (error == null || error === '') return fallback
  if (typeof error === 'string') {
    const text = error.trim()
    if (!text) return fallback
    if (/^request failed/i.test(text) || /^http\s*\d+/i.test(text)) return fallback
    return text
  }
  if (typeof error === 'object') {
    const message = error.message || error.error || error.detail
    return errorText(message, fallback)
  }
  return fallback
}

export function sendData(data) {
  fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify(data)
  })
    .then(async res => {
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(errorText(payload, `请求失败（${res.status}），请稍后重试`))
      }
      window.postMessage({ type: 'bot_response', payload }, window.location.origin)
    })
    .catch(error => {
      console.error('[API error]', error)
      window.postMessage(
        { type: 'bot_response', payload: { error: errorText(error) } },
        window.location.origin
      )
    })
}
