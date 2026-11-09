import { useState } from 'react'
import { contactName } from '../utils/tenant'
import { openTelegramLink } from '../utils/twa'
import './CopyBooking.css'

export default function CopyBooking({ url, compact = false }) {
  const [status, setStatus] = useState('')
  const [failed, setFailed] = useState(false)
  const message = new URL(url).searchParams.get('text') || ''
  if (!message) return null
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message)
      setFailed(false)
      setStatus('已复制，去聊天粘贴发送。')
    } catch (error) {
      console.error('[Copy booking]', error)
      setFailed(true)
      setStatus('复制失败，请长按下方文字手动复制。')
    }
  }
  return (
    <div className="copy-contact">
      {!compact && <p className="copy-contact-hint">打开与{contactName()}的聊天并预填资料；若未带入，请粘贴后发送。</p>}
      <button type="button" className="home-copy-booking copy-contact-button" onClick={() => {
        copy()
        openTelegramLink(url)
      }}>
        复制内容并联系{contactName()}
      </button>
      {status && <p role="status">{status}</p>}
      {failed && <textarea aria-label="预约信息" readOnly value={message}
        style={{ boxSizing: 'border-box', width: '100%', height: '120px', fontSize: '16px', marginTop: '8px' }} />}
    </div>
  )
}
