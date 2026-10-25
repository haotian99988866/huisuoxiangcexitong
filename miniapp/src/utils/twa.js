import { contactUrl, isTenantEntry, tenantHeaders } from './tenant'

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')

const backOverlays = []
let visitTracked = false

export function registerBackOverlay(handler) {
  backOverlays.push(handler)
  return () => {
    const index = backOverlays.lastIndexOf(handler)
    if (index >= 0) backOverlays.splice(index, 1)
  }
}

// 返回箭头始终显示：有详情页时退出当前详情，在列表页时返回 Telegram（关闭小程序）
export function bindBackButton(handler) {
  const tg = window.Telegram?.WebApp
  const button = tg?.BackButton
  if (!button || !handler) return () => {}
  const onBack = () => {
    const overlay = backOverlays[backOverlays.length - 1]
    if (overlay) overlay()
    else handler()
  }
  button.onClick(onBack)
  button.show()
  return () => {
    button.offClick(onBack)
    button.hide()
  }
}

export function closeMiniApp() {
  window.Telegram?.WebApp?.close?.()
}

// t.me 链接交给 Telegram 内部打开(频道原帖);浏览器里退回新窗口
export function openTelegramLink(url) {
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
export const hasTelegramBackButton = Boolean(window.Telegram?.WebApp?.BackButton)

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

export function trackVisit() {
  if (isTenantEntry() || visitTracked) return
  const initData = window.Telegram?.WebApp?.initData
  if (!initData) return
  visitTracked = true
  fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify({ action: 'track_visit', init_data: initData }),
    keepalive: true
  }).catch(() => {})
}

export function sendData(data) {
  fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify(data)
  })
    .then(async res => {
      const payload = await res.json()
      if (!res.ok) {
        throw new Error(payload.error || `Request failed (${res.status})`)
      }
      window.postMessage({ type: 'bot_response', payload }, window.location.origin)
    })
    .catch(error => {
      console.error('[API error]', error)
      window.postMessage(
        { type: 'bot_response', payload: { error: '服务暂时不可用，请稍后重试。' } },
        window.location.origin
      )
    })
}
