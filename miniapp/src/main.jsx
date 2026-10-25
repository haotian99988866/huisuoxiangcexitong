import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { trackVisit, openTelegramLink } from './utils/twa'

const toTelegramDeepLink = href => {
  const url = new URL(href)
  const text = url.searchParams.get('text')
  return `tg://resolve?domain=${url.pathname.slice(1)}${text ? `&text=${encodeURIComponent(text)}` : ''}`
}

// 小程序内 target=_blank 的 t.me 链接部分客户端会拦截,统一交给 Telegram 打开;
// iOS 全屏直达打开时 openTelegramLink 可能无响应,1.5 秒后仍停留在页面则改用 tg:// 协议
document.addEventListener('click', event => {
  const anchor = event.target.closest?.('a[href^="https://t.me/"]')
  const tg = window.Telegram?.WebApp
  if (!anchor || !tg?.openTelegramLink) return
  event.preventDefault()
  try {
    openTelegramLink(anchor.href)
  } catch (error) {
    console.error('[openTelegramLink]', error)
  }
  setTimeout(() => {
    if (document.visibilityState !== 'visible') return
    window.location.href = toTelegramDeepLink(anchor.href)
  }, 1500)
}, true)

trackVisit()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)