const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')

export function sendData(data) {
  fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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