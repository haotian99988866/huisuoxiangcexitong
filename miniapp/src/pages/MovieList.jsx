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
  height: '30px',
  padding: '0 12px',
  borderRadius: '15px',
  border: active ? '1px solid #9a6f35' : '1px solid rgba(174, 151, 122, 0.35)',
  background: active ? '#9a6f35' : '#fff',
  color: active ? '#fff' : '#5a4126',
  fontSize: '13px',
  fontWeight: 600,
  whiteSpace: 'nowrap',
  cursor: 'pointer'
})

function Thumb({ src, style }) {
  return (
    <div style={{ background: '#e9dfd0', overflow: 'hidden', ...style }}>
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
    <div style={{ minHeight: '100vh', background: '#faf6f0', padding: '12px', boxSizing: 'border-box' }}>
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 20,
        padding: '10px 12px',
        margin: '-12px -12px 12px',
        background: 'linear-gradient(180deg, #fffdf8 0%, #faf6f0 100%)',
        borderBottom: '1px solid rgba(158, 132, 101, 0.16)',
        boxShadow: '0 4px 14px rgba(120, 90, 50, 0.07)'
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
              fontSize: '15px', fontWeight: 700, color: '#5a4126',
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
              placeholder="🔍 搜索片名"
              aria-label="搜索片名"
              style={{
                width: '100%', boxSizing: 'border-box', height: '36px', padding: '0 12px',
                border: '1px solid rgba(174, 151, 122, 0.32)', borderRadius: '12px',
                fontSize: '16px', color: '#5a4126', outline: 'none', background: '#fff'
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
          <div style={{ textAlign: 'center', color: '#a08a6b', padding: '40px 0' }}>加载中...</div>
        ) : (
          <div style={{ display: 'grid', gap: '10px' }}>
            {episodes.map(episode => (
              <button
                key={episode.url}
                onClick={() => openMovie(episode.url)}
                style={{
                  display: 'flex', gap: '10px', padding: '8px', textAlign: 'left',
                  background: '#fff', border: '1px solid rgba(174, 151, 122, 0.22)',
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
                <div style={{ flex: 1, minWidth: 0, fontSize: '14px', fontWeight: 600, color: '#5a4126', lineHeight: 1.4 }}>
                  {episode.title}
                </div>
              </button>
            ))}
            {episodes.length === 0 && (
              <div style={{ textAlign: 'center', color: '#a08a6b', padding: '40px 0' }}>暂无内容</div>
            )}
          </div>
        )
      ) : loading ? (
        <div style={{ textAlign: 'center', color: '#a08a6b', padding: '40px 0' }}>加载中...</div>
      ) : error ? (
        <div style={{ textAlign: 'center', color: '#b35a4a', padding: '40px 0' }}>{error}</div>
      ) : data.series.length === 0 ? (
        <div style={{ textAlign: 'center', color: '#a08a6b', padding: '40px 0' }}>
          {query ? '没有找到相关内容' : '暂无内容'}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '12px 10px' }}>
          {data.series.map(item => (
            <button
              key={`${item.category}-${item.name}`}
              onClick={() => openItem(item)}
              style={{ padding: 0, border: 0, background: 'transparent', textAlign: 'left', cursor: 'pointer' }}
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
                marginTop: '5px', fontSize: '13px', fontWeight: 700, color: '#5a4126', lineHeight: 1.35,
                display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden'
              }}>
                {item.name}
              </div>
              <div style={{ fontSize: '11px', color: '#a08a6b', marginTop: '1px' }}>{item.category}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
