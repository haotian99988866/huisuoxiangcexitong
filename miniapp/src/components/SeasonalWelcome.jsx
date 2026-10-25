import { useEffect, useState } from 'react'
import SeasonalOrnament from './SeasonalOrnament'
import InkScenery from './InkScenery'
import './SeasonalWelcome.css'

export default function SeasonalWelcome({ background = false }) {
  const [calendar, setCalendar] = useState(null)
  const [calendarError, setCalendarError] = useState(false)
  useEffect(() => {
    let active = true
    let update
    setCalendarError(false)
    import('../utils/seasonalCalendar').then(({ seasonalCalendar }) => {
      update = () => {
        if (!active) return
        try {
          const next = seasonalCalendar()
          setCalendar(current => current?.dateKey === next.dateKey ? current : next)
          setCalendarError(false)
        } catch (error) {
          console.error('[Seasonal calendar]', error)
          setCalendarError(true)
        }
      }
      update()
    }).catch(error => {
      console.error('[Seasonal calendar load]', error)
      if (active) setCalendarError(true)
    })
    const refresh = () => { if (document.visibilityState === 'visible') update?.() }
    const timer = setInterval(refresh, 60000)
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('focus', refresh)
    return () => {
      active = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('focus', refresh)
    }
  }, [])

  useEffect(() => {
    if (!background || !calendar) return undefined
    // Apply at the root so portalled menus and native dialog top layers share the theme.
    const root = document.documentElement
    const previous = Object.fromEntries(Object.keys(calendar.theme.tokens).map(key =>
      [key, root.style.getPropertyValue(key)]))
    const attributes = {
      'data-seasonal-theme': calendar.theme.id,
      'data-seasonal-motif': calendar.theme.motif,
      'data-ink-layout': calendar.layout.id,
    }
    const previousAttributes = Object.fromEntries(Object.keys(attributes).map(key => [key, root.getAttribute(key)]))
    Object.entries(calendar.theme.tokens).forEach(([key, value]) => root.style.setProperty(key, value))
    Object.entries(attributes).forEach(([key, value]) => root.setAttribute(key, value))
    return () => {
      Object.entries(previous).forEach(([key, value]) => {
        if (value) root.style.setProperty(key, value)
        else root.style.removeProperty(key)
      })
      Object.entries(previousAttributes).forEach(([key, value]) => {
        if (value === null) root.removeAttribute(key)
        else root.setAttribute(key, value)
      })
    }
  }, [background, calendar])

  const theme = `season-${calendar?.season || 'neutral'}${calendar?.festivals.length ? ' is-festive' : ''}`
  if (background) return <div className={`seasonal-background ${theme}`} aria-hidden="true">
    <InkScenery className="ink-landscape" />
    <SeasonalOrnament />
  </div>
  return <section className="seasonal-welcome" aria-label="生活与时令">
    <div className={`seasonal-card ${theme}`}>
      <div className="seasonal-date-row">
        <div className="seasonal-date">
          {calendar ? <><time dateTime={calendar.dateKey}>{calendar.solarLabel} · {calendar.weekday}</time>
            <span>{calendar.lunarLabel}</span></> : <span role={calendarError ? 'alert' : 'status'}>
            {calendarError ? '时令日历暂不可用' : '正在读取今日时令…'}</span>}
        </div>
      </div>
      <div>
        <InkScenery className="seasonal-landscape" compact />
        <SeasonalOrnament />
        <div className="seasonal-copy">
          <div className="seasonal-tags">
            <span>{calendar ? `${calendar.termToday ? '今日节气' : '时值'} · ${calendar.currentTerm}` : '四时有序'}</span>
            {calendar?.festivals.map(festival => <span className="seasonal-festival" key={festival}>{festival}</span>)}
          </div>
          <h2>{calendar?.headline || '把日常，过成好时光'}</h2>
          <p>{calendar?.description || '生活服务、音乐与资讯，在这里轻松发现。'}</p>
        </div>
      </div>
      <footer className="seasonal-calendar-note">
        <span>中国时令 · 北京时间</span>
        {calendar && <span>{calendar.weekday} · {calendar.layout.name}</span>}
        {calendar && <span>{calendar.daysUntilNextTerm} 天后 · {calendar.nextTerm}</span>}
        {calendarError && <button type="button" onClick={() => window.location.reload()}>刷新重试</button>}
      </footer>
    </div>
  </section>
}
