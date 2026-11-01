import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { analyticsId, analyticsPage, analyticsRequest, analyticsScreen, loadAnalyticsPreferences, subscribeAnalytics, subscribeAnalyticsScreen } from '../utils/analytics'
import { isTenantEntry } from '../utils/tenant'

const localDay = () => Math.floor((Date.now() + 7 * 3600000) / 86400000)

export default function AnalyticsTracker() {
  const location = useLocation()
  const session = useRef(null)
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState('')
  const [screen, setScreen] = useState(analyticsScreen())
  const [day, setDay] = useState(localDay)
  const allowed = !isTenantEntry() && Boolean(window.Telegram?.WebApp?.initData)
  useEffect(() => subscribeAnalyticsScreen(setScreen), [])
  useEffect(() => {
    const timer = setInterval(() => setDay(localDay()), 15000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!allowed) return
    let active = true
    const update = value => { if (active) setRecording(value.recording) }
    const unsubscribe = subscribeAnalytics(update)
    loadAnalyticsPreferences().then(update).catch(failure => {
      console.error('[Analytics preferences]', failure)
      if (active) setError('浏览统计连接失败，可在“我的”中重试；页面仍可正常使用。')
    })
    return () => { active = false; unsubscribe() }
  }, [allowed])

  useEffect(() => {
    if (!allowed || !recording) return
    if (!session.current) session.current = analyticsId()
    const view = analyticsId()
    const page = screen || analyticsPage(location.pathname, location.search)
    let total = 0
    let visibleSince = document.visibilityState === 'visible' ? performance.now() : null
    let queue = Promise.resolve()
    let failures = 0
    const account = () => {
      if (visibleSince !== null) {
        const now = performance.now()
        total += Math.max(0, now - visibleSince)
        visibleSince = now
      }
    }
    const send = (keepalive = false) => {
      account()
      const fields = { session_id: session.current, view_id: view, page_key: page, total_ms: Math.floor(total) }
      const deliver = async () => {
        try {
          const result = await analyticsRequest('analytics_record', fields, keepalive)
          if (!result.recording) setRecording(false)
          failures = 0
          setError('')
        } catch (failure) {
          failures++
          console.error('[Analytics heartbeat]', failure)
          if (failures >= 2) setError('浏览统计暂时连接失败，部分停留时长可能未记录；不影响使用。')
        }
      }
      if (keepalive) deliver()
      else queue = queue.then(deliver)
    }
    const visibility = () => {
      if (document.visibilityState === 'hidden') {
        send(true)
        visibleSince = null
      } else {
        visibleSince = performance.now()
        send()
      }
    }
    const exit = () => {
      send(true)
      visibleSince = null
    }
    send()
    const timer = setInterval(() => { if (visibleSince !== null) send() }, 15000)
    document.addEventListener('visibilitychange', visibility)
    window.addEventListener('pagehide', exit)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', visibility)
      window.removeEventListener('pagehide', exit)
      send(true)
    }
  }, [allowed, recording, screen, day, location.pathname, location.search])

  return error ? <div className="analytics-connection" role="status">{error}</div> : null
}
