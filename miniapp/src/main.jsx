import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { trackVisit, openTelegramLink } from './utils/twa'

const debugToast = text => {
  const box = document.createElement('div')
  box.textContent = text
  box.style.cssText = 'position:fixed;left:8px;right:8px;top:120px;z-index:99999;background:#000c;color:#fff;font-size:13px;padding:8px;border-radius:8px;word-break:break-all'
  document.body.appendChild(box)
  setTimeout(() => box.remove(), 4000)
}

// 部分客户端会拦截小程序内 target=_blank 的 t.me 链接,统一交给 Telegram 打开
document.addEventListener('click', event => {
  const anchor = event.target.closest?.('a[href^="https://t.me/"]')
  if (!anchor) return
  const tg = window.Telegram?.WebApp
  debugToast(`点击已到达; tg=${Boolean(tg?.openTelegramLink)} fs=${tg?.isFullscreen} v=${tg?.version}`)
  if (!tg?.openTelegramLink) return
  event.preventDefault()
  try {
    openTelegramLink(anchor.href)
  } catch (error) {
    debugToast(`打开失败: ${error?.message || error}`)
  }
}, true)

trackVisit()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)