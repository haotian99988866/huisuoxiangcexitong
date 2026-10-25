import { useEffect, useState } from 'react'
import { AnnouncementCard, FullscreenAnnouncementBar } from '../components/Announcement'
import ModuleTabs from '../components/ModuleTabs'
import { useModuleState, useModuleScroll } from '../utils/moduleState'
import { mediaUrl, tenantHeaders } from '../utils/tenant'
import {
  API_BASE_URL,
  bindBackButton,
  closeMiniApp,
  hasTelegramBackButton,
  openTelegramLink,
  subscribeFullscreen
} from '../utils/twa'

const coverUrl = cover => {
  if (!cover) return ''
  return /^https?:/.test(cover) ? cover : mediaUrl(`${API_BASE_URL}/${cover}`)
}

const openMovie = href => {
  const url = new URL(href)
  url.searchParams.set('t', '1')
  openTelegramLink(url.href)
}

const formatDuration = seconds => {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const pad = n => String(n).padStart(2, '0')
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

async function post(body) {
  const res = await fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify(body)
  })
  const payload = await res.json()
  if (!res.ok) throw new Error(payload.error || '请求失败')
  return payload
}

const chipStyle = active => ({
  flex: '0 0 auto',
  minHeight: '44px',
  padding: '0 12px',
  borderRadius: '15px',
  border: active ? '1px solid var(--ink-accent)' : '1px solid var(--ink-line)',
  background: active ? 'var(--ink-accent)' : 'var(--ink-surface)',
  color: active ? '#fffef5' : 'var(--ink-text)',
  fontSize: '13px',
  fontWeight: 600,
  whiteSpace: 'nowrap',
  cursor: 'pointer'
})

function Thumb({ src, style }) {
  return (
    <div style={{ background: '#e2e7dc', overflow: 'hidden', ...style }}>
      {src && (
        <img
          src={coverUrl(src)}
          alt=""
          loading="lazy"
          decoding="async"
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      )}
    </div>
  )
}

export default function MovieList() {
  const [isFullscreen, setIsFullscreen] = useState(() => Boolean(window.Telegram?.WebApp?.isFullscreen))
  useEffect(() => subscribeFullscreen(setIsFullscreen), [])
  const [category, setCategory] = useModuleState('movies.category', '')
  const [queryInput, setQueryInput] = useModuleState('movies.searchInput', '')
  const [query, setQuery] = useModuleState('movies.search', '')
  const [data, setData] = useState({ categories: [], series: [], total: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [openSeries, setOpenSeries] = useState(null)
  const [episodes, setEpisodes] = useState([])
  const [episodesLoading, setEpisodesLoading] = useState(false)
  useModuleScroll('movies', !loading)

  useEffect(() => {
    const timer = setTimeout(() => setQuery(queryInput.trim()), 300)
    return () => clearTimeout(timer)
  }, [queryInput])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    post({ action: 'get_movies', category, query })
      .then(result => !cancelled && setData(result))
      .catch(() => !cancelled && setError('加载失败，请稍后重试'))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [category, query])

  useEffect(() => {
    return bindBackButton(() => {
      if (openSeries) setOpenSeries(null)
      else closeMiniApp()
    })
  }, [openSeries])

  const openItem = item => {
    if (item.url) {
      openMovie(item.url)
      return
    }
    setOpenSeries(item)
    setEpisodes([])
    setEpisodesLoading(true)
    window.scrollTo(0, 0)
    post({ action: 'get_movie_series', series: item.name })
      .then(result => setEpisodes(result.episodes || []))
      .catch(() => setEpisodes([]))
      .finally(() => setEpisodesLoading(false))
  }

  return (
    <div className="movies-page" style={{ minHeight: '100vh', padding: '12px', boxSizing: 'border-box' }}>
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 20,
        padding: '10px 12px',
        margin: '-12px -12px 12px',
        background: 'var(--ink-header)',
        borderBottom: '1px solid var(--ink-line)',
        boxShadow: '0 3px 12px rgba(44,65,48,0.03)'
      }}>
        {isFullscreen && <FullscreenAnnouncementBar />}
        <ModuleTabs active="movies" />
        {!isFullscreen && !openSeries && <AnnouncementCard />}
        {openSeries ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {!hasTelegramBackButton && (
              <button
                onClick={() => setOpenSeries(null)}
                style={{ ...chipStyle(false), height: '34px' }}
              >
                ← 返回
              </button>
            )}
            <div style={{
              fontSize: '15px', fontWeight: 700, color: 'var(--ink-text)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
            }}>
              {openSeries.name}
            </div>
          </div>
        ) : (
          <>
            <input
              value={queryInput}
              onChange={e => setQueryInput(e.target.value)}
              placeholder="搜索片名"
              aria-label="搜索片名"
              style={{
                width: '100%', boxSizing: 'border-box', height: '36px', padding: '0 12px',
                border: '1px solid var(--ink-line)', borderRadius: '12px',
                fontSize: '16px', color: 'var(--ink-text)', background: 'var(--ink-reading)'
              }}
            />
            <div style={{ display: 'flex', gap: '6px', overflowX: 'auto', marginTop: '8px', paddingBottom: '2px' }}>
              <button style={chipStyle(!category)} onClick={() => setCategory('')}>
                全部 {data.total || ''}
              </button>
              {data.categories.map(item => (
                <button
                  key={item.name}
                  style={chipStyle(category === item.name)}
                  onClick={() => setCategory(item.name)}
                >
                  {item.name} {item.count}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {openSeries ? (
        episodesLoading ? (
          <div style={{ textAlign: 'center', color: 'var(--ink-muted)', padding: '40px 0' }}>加载中...</div>
        ) : (
          <div style={{ display: 'grid', gap: '10px' }}>
            {episodes.map(episode => (
              <button
                key={episode.url}
                onClick={() => openMovie(episode.url)}
                style={{
                  display: 'flex', gap: '10px', padding: '8px', textAlign: 'left',
                  background: 'var(--ink-surface)', border: '1px solid var(--ink-line)',
                  borderRadius: '12px', cursor: 'pointer'
                }}
              >
                <div style={{ position: 'relative', flex: '0 0 128px' }}>
                  <Thumb src={episode.thumb} style={{ width: '128px', aspectRatio: '16 / 9', borderRadius: '8px' }} />
                  <span style={{
                    position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
                    justifyContent: 'center', color: '#fff', fontSize: '22px',
                    textShadow: '0 1px 6px rgba(0,0,0,0.5)'
                  }}>▶</span>
                  {episode.duration ? (
                    <span style={{
                      position: 'absolute', right: '4px', bottom: '4px', padding: '1px 5px',
                      borderRadius: '4px', background: 'rgba(0,0,0,0.6)', color: '#fff', fontSize: '11px'
                    }}>{formatDuration(episode.duration)}</span>
                  ) : null}
                </div>
                <div style={{ flex: 1, minWidth: 0, fontSize: '14px', fontWeight: 600, color: 'var(--ink-text)', lineHeight: 1.4 }}>
                  {episode.title}
                </div>
              </button>
            ))}
            {episodes.length === 0 && (
              <div style={{ textAlign: 'center', color: 'var(--ink-muted)', padding: '40px 0' }}>暂无内容</div>
            )}
          </div>
        )
      ) : loading ? (
        <div style={{ textAlign: 'center', color: 'var(--ink-muted)', padding: '40px 0' }}>加载中...</div>
      ) : error ? (
        <div style={{ textAlign: 'center', color: '#b35a4a', padding: '40px 0' }}>{error}</div>
      ) : data.series.length === 0 ? (
        <div style={{ textAlign: 'center', color: 'var(--ink-muted)', padding: '40px 0' }}>
          {query ? '没有找到相关内容' : '暂无内容'}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '12px 10px' }}>
          {data.series.map(item => (
            <button
              key={`${item.category}-${item.name}`}
              onClick={() => openItem(item)}
              style={{ padding: '8px', border: '1px solid var(--ink-line)', borderRadius: '14px', background: 'var(--ink-surface)', textAlign: 'left', cursor: 'pointer' }}
            >
              <div style={{ position: 'relative' }}>
                <Thumb src={item.cover} style={{ width: '100%', aspectRatio: '16 / 9', borderRadius: '10px' }} />
                {item.episodes > 1 && (
                  <span style={{
                    position: 'absolute', right: '6px', bottom: '6px', padding: '1px 7px',
                    borderRadius: '10px', background: 'rgba(0,0,0,0.62)', color: '#fff', fontSize: '11px'
                  }}>共 {item.episodes} 集</span>
                )}
              </div>
              <div style={{
                marginTop: '5px', fontSize: '13px', fontWeight: 700, color: 'var(--ink-text)', lineHeight: 1.35,
                display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden'
              }}>
                {item.name}
              </div>
              <div style={{ fontSize: '11px', color: 'var(--ink-muted)', marginTop: '1px' }}>{item.category}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
