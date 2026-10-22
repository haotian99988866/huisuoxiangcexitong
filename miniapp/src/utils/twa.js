// 直连模式：手机和电脑用相同 localhost 即可通信
const BACKEND_URL = 'http://127.0.0.1:8000'

export function sendData(data) {
  console.log('[发送]', BACKEND_URL, data)
  fetch(`${BACKEND_URL}/webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  .then(res => res.json())
  .then(res => {
    console.log('[收到]', res)
    window.postMessage({ type: 'bot_response', payload: res }, '*')
  })
  .catch(e => console.error('[错误]', e))
}