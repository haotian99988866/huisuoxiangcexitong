import { useState } from 'react'

export default function CopyBooking({ url }) {
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
    <div style={{ marginTop: '10px', fontSize: '12px', color: '#888', textAlign: 'center' }}>
      <p style={{ margin: '0 0 6px', lineHeight: 1.5 }}>联系昊天后，若聊天框未带入资料，请复制后粘贴发送。</p>
      <button type="button" onClick={copy}
        style={{ padding: '8px 14px', marginTop: '6px', border: '1px solid #e0d2bd', borderRadius: '8px', background: '#fffaf0', color: '#80643c' }}>
        复制预约资料
      </button>
      {status && <p role="status">{status}</p>}
      {failed && <textarea aria-label="预约信息" readOnly value={message}
        style={{ width: '100%', height: '120px', fontSize: '16px', marginTop: '8px' }} />}
    </div>
  )
}
