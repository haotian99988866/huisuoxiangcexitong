import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { trackVisit, openTelegramLink } from './utils/twa'

// 部分客户端会拦截小程序内 target=_blank 的 t.me 链接,统一交给 Telegram 打开
document.addEventListener('click', event => {
  const anchor = event.target.closest?.('a[href^="https://t.me/"]')
  if (!anchor || !window.Telegram?.WebApp?.openTelegramLink) return
  event.preventDefault()
  openTelegramLink(anchor.href)
})

trackVisit()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)