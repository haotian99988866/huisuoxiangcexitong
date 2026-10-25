import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { openTelegramLink } from './utils/twa'

const toTelegramDeepLink = href => {
  const url = new URL(href)
  const text = url.searchParams.get('text')
  return `tg://resolve?domain=${url.pathname.slice(1)}${text ? `&text=${encodeURIComponent(text)}` : ''}`
}

// 小程序内 target=_blank 的 t.me 链接部分客户端会拦截,统一交给 Telegram 打开;
// iOS 客服直达与网页入口分开测试，其余链接保留协议兜底。
document.addEventListener('click', event => {
  const anchor = event.target.closest?.('a[href^="https://t.me/"]')
  const tg = window.Telegram?.WebApp
  if (!anchor || !tg?.openTelegramLink) return
  event.preventDefault()
  try {
    if (anchor.dataset.telegramBrowser === 'true' && tg.openLink) {
      tg.openLink(anchor.href, { try_browser: 'safari' })
      return
    }
    if (openTelegramLink(anchor.href) === 'contact-direct') return
  } catch (error) {
    console.error('[openTelegramLink]', error)
    if (tg.platform === 'ios') {
      tg.showAlert?.('无法打开联系人，请尝试「通过网页联系」，或在 Telegram 搜索联系人用户名。')
      return
    }
  }
  setTimeout(() => {
    if (document.visibilityState !== 'visible') return
    window.location.href = toTelegramDeepLink(anchor.href)
  }, 1500)
}, true)

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)