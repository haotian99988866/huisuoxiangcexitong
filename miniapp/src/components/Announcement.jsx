import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { announcementText, hasAnnouncementAccess, tenantPath } from '../utils/tenant'
import { requestAnnouncements } from '../utils/announcements'
import './Announcement.css'

let snapshot = { text: '资讯加载中 · 点击查看历史', id: null, publishedAt: null }
const listeners = new Set()
let fetching = false
let updated = 0
async function refreshAnnouncement() {
  if (fetching || Date.now() - updated < 60000) return
  fetching = true
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15000)
  try {
    const result = await requestAnnouncements('get_announcements', {}, controller.signal)
    const latest = result.items[0]
    snapshot = { text: latest?.title || latest?.summary || '暂无公告 · 查看历史', id: latest?.id || null, publishedAt: latest?.published_at || null }
    updated = Date.now()
  } catch (error) {
    console.error('[Announcement preview]', error)
    snapshot = { text: '公告暂不可用 · 点击查看并重试', id: null, publishedAt: null }
  } finally {
    clearTimeout(timer)
    fetching = false
    listeners.forEach(listener => listener(snapshot))
  }
}

function ScrollingTitle({ text, publishedAt }) {
  const viewport = useRef(null)
  const content = useRef(null)
  const [distance, setDistance] = useState(0)
  useLayoutEffect(() => {
    const measure = () => setDistance(Math.max(0, content.current.scrollWidth - viewport.current.clientWidth))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(viewport.current)
    observer.observe(content.current)
    return () => observer.disconnect()
  }, [text, publishedAt])
  return <span ref={viewport} className="announcement-ticker">
    <span key={text} ref={content} className={`announcement-card-title${distance ? ' is-scrolling' : ''}`}
      style={{ '--ticker-offset': `-${distance}px`, '--ticker-duration': `${Math.max(12, distance / 26 + 5)}s` }}>
      <span className="announcement-card-label">东南亚大事件 · </span>
      {publishedAt && <time className="announcement-card-time" dateTime={publishedAt}>
        {new Date(publishedAt).toLocaleString('zh-CN', {
          month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
        })}
      </time>}
      {publishedAt && '· '}
      {text}
    </span>
  </span>
}

function AnnouncementPreview() {
  const [latest, setLatest] = useState(snapshot)
  const allowed = hasAnnouncementAccess()
  useEffect(() => {
    if (!allowed) return
    listeners.add(setLatest)
    refreshAnnouncement()
    const timer = setInterval(refreshAnnouncement, 60000)
    return () => { listeners.delete(setLatest); clearInterval(timer) }
  }, [allowed])
  if (!allowed) return <div className="announcement-notice">{announcementText()}</div>
  return <Link className="announcement-card" to={tenantPath(latest.id ? `/announcements/${latest.id}` : '/announcements')}
    aria-label={`东南亚大事件：${latest.text}${latest.publishedAt ? `，发布于${new Date(latest.publishedAt).toLocaleString('zh-CN')}` : ''}，查看${latest.id ? '详情' : '列表'}`}>
    <span className="announcement-card-label" aria-hidden="true">📢</span>
    <ScrollingTitle text={latest.text} publishedAt={latest.publishedAt} />
    <span className="announcement-card-action" aria-hidden="true">›</span>
  </Link>
}

export function FullscreenAnnouncementBar() {
  return (
    <div className="announcement-fullscreen">
      <div className="announcement-native-clearance">
        <AnnouncementCard />
      </div>
    </div>
  )
}

export function AnnouncementCard() {
  return (
    <div className="announcement-container" aria-label="公告">
      <AnnouncementPreview />
    </div>
  )
}
