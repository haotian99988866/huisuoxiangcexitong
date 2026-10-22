export function sendData(data) {
  console.log('[发送]', data)
  fetch('http://127.0.0.1:8000/webhook', {
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