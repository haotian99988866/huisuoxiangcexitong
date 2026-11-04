import { useEffect, useRef, useState } from 'react'
import { isTenantEntry } from './tenant'
import { newMemberOrderId } from './membership'
import { requestCustomerDuty } from './customerDuty'

export default function useCustomerSubscriptions(venueId, onRefresh, { delivery = false, enabled = true } = {}) {
  const [states, setStates] = useState(null)
  const [pending, setPending] = useState({})
  const [busy, setBusy] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [notice, setNotice] = useState('')
  const [corrupt, setCorrupt] = useState(false)
  const mounted = useRef(false)
  const lock = useRef(false)
  const refreshLock = useRef(false)
  const authenticated = enabled && Boolean(window.Telegram?.WebApp?.initData) && !isTenantEntry()
  const prefix = delivery ? 'delivery' : 'staff'
  const identity = delivery ? 'person_id' : 'staff_id'
  const frequency = delivery ? '每个晚班' : '白班、晚班每班'
  const user = window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'browser'
  const key = `${prefix}-customer-${delivery ? '' : 'catalog-'}pending-${user}-${venueId}`
  const validPending = (id, value) => value && Number.isSafeInteger(value[identity]) && value[identity] > 0 &&
    String(value[identity]) === id && /^[0-9a-f]{32}$/.test(value.request_id) &&
    typeof value.enabled === 'boolean' && Number.isInteger(value.revision) && value.revision >= 0
  const refresh = async () => {
    if (!authenticated || refreshLock.current || lock.current) return
    refreshLock.current = true
    if (mounted.current) setRefreshing(true)
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || '{}')
      if (!saved || typeof saved !== 'object' || Array.isArray(saved) ||
          Object.entries(saved).some(([id, value]) => !validPending(id, value))) {
        throw new Error('暂存订阅操作损坏，请先人工核对，不要重复操作')
      }
      const result = await requestCustomerDuty(delivery ? 'delivery_customer_status' : 'staff_customer_catalog', { venue_id: venueId })
      if (!delivery) {
        for (const id of Object.keys(result.states)) {
          const legacy = sessionStorage.getItem(`staff-customer-pending-${user}-${id}`)
          if (!legacy) continue
          const payload = JSON.parse(legacy)
          if (!validPending(id, payload) || (saved[id] && saved[id].request_id !== payload.request_id)) {
            throw new Error('暂存订阅操作冲突或损坏，请先人工核对，不要重复操作')
          }
          saved[id] = payload
        }
        sessionStorage.setItem(key, JSON.stringify(saved))
      }
      if (mounted.current) {
        setPending(saved)
        setStates(result)
        setCorrupt(false)
        setLoadError('')
      }
    } catch (failure) {
      console.error('[Customer subscription status]', failure)
      if (mounted.current) {
        setCorrupt(true)
        setLoadError(failure.name === 'AbortError' ? '订阅读取超时，请重试' : failure.message)
      }
    } finally {
      refreshLock.current = false
      if (mounted.current) setRefreshing(false)
    }
  }
  useEffect(() => {
    mounted.current = true
    refresh()
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && !lock.current) refresh()
    }, 30000)
    return () => { mounted.current = false; clearInterval(timer) }
  }, [venueId, authenticated, delivery])
  useEffect(() => {
    if (!states) return
    const timer = setTimeout(() => { refresh(); onRefresh() },
      Math.max(1000, states.shift_ends_at - Date.now()))
    return () => clearTimeout(timer)
  }, [states?.shift_ends_at])
  const toggle = async personId => {
    const state = states?.states[personId]
    if (lock.current || refreshLock.current || !authenticated || corrupt || !state) return
    lock.current = true
    setBusy(personId)
    setError('')
    setNotice('')
    const payload = pending[personId] || {
      [identity]: personId, enabled: !state.subscribed, revision: state.revision, request_id: newMemberOrderId(),
    }
    const next = { ...pending, [personId]: payload }
    try {
      sessionStorage.setItem(key, JSON.stringify(next))
      setPending(next)
      const result = await requestCustomerDuty(`${prefix}_customer_subscribe`, payload)
      const remaining = { ...next }
      delete remaining[personId]
      sessionStorage.setItem(key, JSON.stringify(remaining))
      if (!delivery) sessionStorage.removeItem(`staff-customer-pending-${user}-${personId}`)
      if (mounted.current) {
        setPending(remaining)
        setStates(current => ({ ...current, states: { ...current.states, [personId]: result } }))
        setNotice(result.subscribed
          ? `档案 #${personId} 已持续订阅，${frequency}新发布出勤时提醒一次。请先在机器人聊天中点击开始。`
          : `档案 #${personId} 已取消订阅，尚未领取发送的提醒已停止。`)
      }
    } catch (failure) {
      console.error('[Customer subscription save]', failure)
      if ([400, 401, 403, 409].includes(failure.status)) {
        const remaining = { ...next }
        delete remaining[personId]
        try {
          sessionStorage.setItem(key, JSON.stringify(remaining))
          if (!delivery) sessionStorage.removeItem(`staff-customer-pending-${user}-${personId}`)
          if (mounted.current) setPending(remaining)
        } catch (storageFailure) {
          console.error('[Customer subscription pending storage]', storageFailure)
          if (mounted.current) setCorrupt(true)
        }
      }
      if (mounted.current) setError(['AbortError', 'TypeError'].includes(failure.name)
        ? '网络异常，结果尚未确认，请用原编号重试，勿重复订阅。' : failure.message)
    } finally {
      lock.current = false
      if (mounted.current) {
        setBusy(null)
        await refresh()
        onRefresh()
      }
    }
  }
  return { states: states?.states, pending, busy, refreshing, error: error || loadError, notice, corrupt, authenticated, toggle, refresh }
}
