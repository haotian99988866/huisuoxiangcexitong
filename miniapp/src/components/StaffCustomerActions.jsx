import { useEffect, useRef, useState } from 'react'
import { API_BASE_URL, openTelegramLink } from '../utils/twa'
import { contactName, contactUrl, isTenantEntry, tenantHeaders } from '../utils/tenant'
import { newMemberOrderId } from '../utils/membership'

async function request(action, fields) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 15000)
  try {
    const response = await fetch(`${API_BASE_URL}/webhook`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
      body: JSON.stringify({ ...fields, action, init_data: window.Telegram?.WebApp?.initData }),
      cache: 'no-store', signal: controller.signal,
    })
    const result = await response.json()
    if (!response.ok) {
      const error = new Error(result.error || '出勤订阅请求失败')
      error.status = response.status
      throw error
    }
    return result
  } finally { clearTimeout(timeout) }
}

export default function StaffCustomerActions({ staffId, onState }) {
  const [state, setState] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState(null)
  const [corrupt, setCorrupt] = useState(false)
  const lock = useRef(false)
  const mounted = useRef(false)
  const authenticated = Boolean(window.Telegram?.WebApp?.initData) && !isTenantEntry()
  const key = `staff-customer-pending-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'browser'}-${staffId}`
  const update = result => { setState(result); onState(result) }
  const refresh = async () => {
    const saved = sessionStorage.getItem(key)
    if (saved) {
      const payload = JSON.parse(saved)
      if (payload.staff_id !== staffId || !/^[0-9a-f]{32}$/.test(payload.request_id) ||
          typeof payload.enabled !== 'boolean' || !Number.isInteger(payload.revision) || payload.revision < 0) {
        throw new Error('暂存订阅操作损坏，请先人工核对，不要重复操作')
      }
      if (mounted.current) setPending(payload)
    }
    const result = await request(authenticated ? 'staff_customer_status' : 'get_staff_detail', { staff_id: staffId })
    if (mounted.current) { setCorrupt(false); update(authenticated ? result : result.customer_duty) }
  }
  useEffect(() => {
    mounted.current = true
    const load = async () => {
      try {
        await refresh()
      } catch (failure) {
        console.error('[Customer duty status]', failure)
        if (mounted.current) {
          setCorrupt(true)
          setError(failure.name === 'AbortError' ? '出勤读取超时，请重新读取' : failure.message)
        }
      }
    }
    load()
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && !lock.current) load()
    }, 30000)
    return () => { mounted.current = false; clearInterval(timer) }
  }, [staffId])
  useEffect(() => {
    if (!state) return
    const timer = setTimeout(() => {
      setState(null); onState(null)
      refresh().catch(failure => {
        console.error('[Customer duty shift change]', failure)
        if (mounted.current) setError(failure.message)
      })
    }, Math.max(0, state.shift_ends_at - Date.now()))
    return () => clearTimeout(timer)
  }, [state?.shift_ends_at])
  const save = async () => {
    if (lock.current || !state || !authenticated || corrupt) return
    lock.current = true; setBusy(true); setError(''); setNotice('')
    const payload = pending || { staff_id: staffId, enabled: !state.subscribed, revision: state.revision, request_id: newMemberOrderId() }
    try {
      sessionStorage.setItem(key, JSON.stringify(payload)); setPending(payload)
      const result = await request('staff_customer_subscribe', payload)
      sessionStorage.removeItem(key)
      if (mounted.current) {
        setPending(null); update(result)
        setNotice(result.subscribed ? '已订阅，机器人将私信提醒新的出勤发布。请先在机器人聊天中点击开始。' : '已取消，尚未领取发送的提醒已停止。')
      }
    } catch (failure) {
      console.error('[Customer duty subscription]', failure)
      if ([400, 401, 403, 409].includes(failure.status)) {
        sessionStorage.removeItem(key)
        if (mounted.current) setPending(null)
      }
      if (mounted.current) setError(failure.name === 'AbortError' ? '结果尚未确认，请用原编号重试' : failure.message)
    } finally {
      lock.current = false
      if (mounted.current) { setBusy(false); refresh().catch(failure => {
        console.error('[Refresh customer duty]', failure)
        if (mounted.current) setError(failure.message)
      }) }
    }
  }
  return <div className="staff-customer-actions">
    {error && <p className="membership-error" role="alert">{error}</p>}
    {notice && <p className="membership-notice" role="status">{notice}</p>}
    {!state ? <button type="button" disabled={busy} onClick={() => refresh().catch(failure => {
      console.error('[Customer duty reload]', failure); setError(failure.message)
    })}>重新读取出勤</button> : <>
      {state.published_at && !pending && !corrupt && <button type="button" className="staff-customer-primary"
        onClick={() => openTelegramLink(contactUrl())}>联系{contactName()} · 正规按摩咨询</button>}
      {(!state.published_at || state.subscribed || pending) && <button type="button"
        className={!state.published_at ? 'staff-customer-primary' : ''}
        disabled={busy || corrupt || !authenticated || (!pending && !state.subscribed && !state.available)}
        onClick={save}>{busy ? '保存中…' : pending ? '结果待核对 · 原编号重试' : state.subscribed ? '已订阅 · 取消订阅' : '订阅出勤提醒'}</button>}
      <p className="staff-meta">{!authenticated && !state.published_at ? '请从 Telegram 主入口打开，才能订阅个人出勤。' :
        !state.available && !state.subscribed && !state.published_at ? '人员尚未开放客人订阅授权。' :
          '只提醒订阅后的新发布，不补发历史；发送结果不明不重发。出勤发布不代表实时在岗。'}</p>
      {corrupt && <button type="button" disabled={busy} onClick={() => refresh().catch(failure => {
        console.error('[Customer duty reload]', failure); setError(failure.message)
      })}>重新核对出勤</button>}
    </>}
  </div>
}
