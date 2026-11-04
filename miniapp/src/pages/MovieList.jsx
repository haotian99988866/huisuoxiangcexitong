import { useEffect, useRef, useState } from 'react'
import { FullscreenAnnouncementBar } from '../components/Announcement'
import ModuleTabs from '../components/ModuleTabs'
import ModuleIcon from '../components/ModuleIcon'
import { rememberModuleScroll, useModuleState, useModuleScroll } from '../utils/moduleState'
import { mediaUrl, tenantHeaders } from '../utils/tenant'
import { movieSummary } from '../utils/catalogSummary'
import { useAnalyticsScreen } from '../utils/analytics'
import { API_BASE_URL, bindBackButton, hasTelegramBackButton, openTelegramLink, subscribeFullscreen } from '../utils/twa'
import './MovieList.css'
import usePagedCatalog from '../utils/usePagedCatalog'
import { ListRefresh, LoadMore } from '../components/ContinuousList'

const seriesKey = item => JSON.stringify([item.category, item.name])
const coverUrl = cover => !cover ? '' : /^https?:/.test(cover) ? cover : mediaUrl(`${API_BASE_URL}/${cover}`)
export const openMovie = href => {
  const url = new URL(href)
  url.searchParams.set('t', '1')
  openTelegramLink(url.href)
}
export const durationText = seconds => {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor(seconds % 3600 / 60)
  const s = seconds % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}
async function post(body, signal) {
  const res = await fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify(body), signal,
  })
  const payload = await res.json()
  if (!res.ok) throw new Error(payload.error || '影视请求失败，请稍后重试')
  return payload
}
export function Cover({ src, label = '影视片库' }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])
  return <div className="movie-cover">
    {src && !failed ? <img src={coverUrl(src)} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
      : <span className="movie-cover-placeholder" aria-hidden="true"><ModuleIcon name="movies" /><small>{label}</small></span>}
  </div>
}

export default function MovieList() {
  const [fullscreen, setFullscreen] = useState(false)
  const [category, setCategory] = useModuleState('movies.category', '')
  const [queryInput, setQueryInput] = useModuleState('movies.searchInput', '')
  const [query, setQuery] = useModuleState('movies.search', '')
  const catalog = usePagedCatalog('movies', { action: 'get_movies', category, query, limit: 24 }, 'series', seriesKey)
  const data = catalog.data || { categories: [], series: [], total: 0 }
  const { loading, error } = catalog
  const [attempt, setAttempt] = useState(0)
  const [openSeries, setOpenSeries] = useState(null)
  const [episodeData, setEpisodeData] = useState({ loading: true, episodes: [], error: '' })
  const searchRef = useRef(null)
  useModuleScroll('movies', catalog.ready && !openSeries)
  useEffect(() => subscribeFullscreen(setFullscreen), [])
  useEffect(() => {
    const timer = setTimeout(() => setQuery(queryInput.trim()), 350)
    return () => clearTimeout(timer)
  }, [queryInput])
  useEffect(() => {
    if (!openSeries) return undefined
    let active = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    setEpisodeData({ loading: true, episodes: [], error: '' })
    post({ action: 'get_movie_series', series: openSeries.name }, controller.signal)
      .then(result => { if (active) setEpisodeData({ loading: false, episodes: result.episodes, error: '' }) })
      .catch(failure => {
        if (active) setEpisodeData({ loading: false, episodes: [], error: failure.name === 'AbortError' ? '选集加载超时，请重试' : failure.message })
      }).finally(() => clearTimeout(timer))
    return () => { active = false; clearTimeout(timer); controller.abort() }
  }, [openSeries, attempt])
  useEffect(() => bindBackButton(openSeries ? () => setOpenSeries(null) : null), [openSeries])
  useAnalyticsScreen(openSeries ? 'movie_detail' : null)

  function openItem(item) {
    if (item.url) { rememberModuleScroll('movies'); openMovie(item.url); return }
    rememberModuleScroll('movies')
    setOpenSeries(item)
  }
  return <main className="movies-page">
    <header className="movie-navigation">
      {fullscreen && <FullscreenAnnouncementBar />}
      <ModuleTabs active="movies" />
      {!openSeries && <form className="movie-search" role="search" onSubmit={event => {
        event.preventDefault(); setQuery(queryInput.trim()); searchRef.current?.blur()
      }}>
        <ModuleIcon name="search" />
        <input ref={searchRef} type="search" aria-label="搜索片名" placeholder="搜索电影、电视剧、综艺"
          value={queryInput} maxLength={50} enterKeyHint="search" onChange={event => setQueryInput(event.target.value)} />
        {queryInput && <button type="button" aria-label="清空片名搜索" onClick={() => { setQueryInput(''); setQuery('') }}>✕</button>}
        <button type="submit">搜索</button>
      </form>}
      {!openSeries && <nav className="movie-categories" aria-label="影视分类">
        <button type="button" aria-pressed={!category} onClick={() => setCategory('')}>全部</button>
        {data.categories.map(item => <button type="button" key={item.name}
          aria-pressed={category === item.name} onClick={() => setCategory(item.name)}>{item.name}</button>)}
      </nav>}
    </header>
    {!openSeries && <ListRefresh onRefresh={catalog.refresh} busy={catalog.refreshing} disabled={catalog.busy} />}
    {openSeries ? <>
      {!hasTelegramBackButton && <button className="movie-back" type="button" onClick={() => setOpenSeries(null)}>← 返回片库</button>}
      <section className="movie-detail">
        <Cover src={openSeries.cover} />
        <div><span className="movie-kicker">{openSeries.category || '影视'}</span><h1>{openSeries.name}</h1>
          <p>共 {openSeries.episodes} 集 · 选择剧集后在 Telegram 中播放</p></div>
      </section>
      <div className="movie-section-heading"><h2>选集播放</h2><span>{episodeData.loading ? '读取中…' : episodeData.error ? '加载失败' : `${episodeData.episodes.length} 个播放入口`}</span></div>
      {episodeData.loading ? <p className="movie-state" role="status">正在加载剧集…</p>
        : episodeData.error ? <div className="movie-state" role="alert"><p>{episodeData.error}</p><button onClick={() => setAttempt(value => value + 1)}>重新加载选集</button></div>
          : <div className="movie-episodes">
            {episodeData.episodes.map((episode, index) => <button type="button" key={episode.url} onClick={() => openMovie(episode.url)}>
              <span className="movie-episode-number">{String(index + 1).padStart(2, '0')}</span>
              <span><strong>{episode.title}</strong><small>{durationText(episode.duration) || '在 Telegram 中播放'}</small></span>
              <span aria-hidden="true">▷</span>
            </button>)}
            {!episodeData.episodes.length && <p className="movie-state">暂无可用剧集。</p>}
          </div>}
    </> : <>
      <div className="movie-section-heading"><div>
        <h1>{query ? `“${query}”的搜索结果` : category || '全部影片'}</h1>
        <p>{loading ? '读取中…' : error ? '加载失败' : movieSummary(data, category, query)}</p></div>
      </div>
      {loading ? <div className="movie-grid movie-skeleton" role="status" aria-label="正在加载影视">
        {Array.from({ length: 6 }, (_, index) => <div className="movie-skeleton-card" key={index} />)}
      </div> : error && !catalog.ready ? <div className="movie-state" role="alert"><p>{error}</p><button onClick={catalog.refresh}>重新加载片库</button></div>
        : !data.series.length ? <div className="movie-state"><ModuleIcon name="search" /><h2>{query ? '没有找到相关影片' : '暂无影片'}</h2>
          <p>试试其他分类或更短的片名。</p><button onClick={() => { setCategory(''); setQueryInput(''); setQuery('') }}>查看全部影片</button></div>
          : <div className="movie-grid">{data.series.map(item => <button type="button" className="movie-card"
            key={seriesKey(item)} onClick={() => openItem(item)}>
            <div className="movie-poster"><Cover src={item.cover} />
              <span className="movie-card-badge">{item.url ? '直接播放' : `共 ${item.episodes} 集`}</span>
              <span className="movie-card-play" aria-hidden="true">▷</span>
            </div>
            <h2>{item.name}</h2><p>{item.category || '影视'} · {item.url ? '在 Telegram 中播放' : '点击选集'}</p>
          </button>)}</div>}
      {catalog.ready && <LoadMore hasMore={data.has_more} busy={catalog.busy} error={error}
        onLoad={catalog.loadMore} />}
    </>}
  </main>
}
