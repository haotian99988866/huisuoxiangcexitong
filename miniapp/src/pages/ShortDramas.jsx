import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import AccountHeader from '../components/AccountHeader'
import ModuleIcon from '../components/ModuleIcon'
import { Cover, durationText, openMovie } from './MovieList'
import { rememberModuleScroll, useModuleScroll, useModuleState } from '../utils/moduleState'
import { API_BASE_URL, bindBackButton } from '../utils/twa'
import { tenantHeaders } from '../utils/tenant'
import './MovieList.css'

export default function ShortDramas() {
  const navigate = useNavigate()
  const [input, setInput] = useModuleState('shortDramas.input', '')
  const [filter, setFilter] = useModuleState('shortDramas.filter', { query: '', page: 1 })
  const [data, setData] = useState({ items: [], total: 0, has_more: false })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const search = useRef(null)
  useModuleScroll('shortDramas', !loading)
  useEffect(() => bindBackButton(() => navigate('/services')), [navigate])
  const applySearch = value => setFilter(previous => previous.query === value
    ? previous : { query: value, page: 1 })
  useEffect(() => {
    const timer = setTimeout(() => setFilter(previous => previous.query === input.trim()
      ? previous : { query: input.trim(), page: 1 }), 350)
    return () => clearTimeout(timer)
  }, [input, setFilter])
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    setLoading(true)
    setError('')
    async function load() {
      try {
        const response = await fetch(`${API_BASE_URL}/webhook`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
          body: JSON.stringify({ action: 'get_short_dramas', ...filter }), signal: controller.signal,
        })
        const result = await response.json()
        if (!response.ok) throw new Error(result.error || '短剧加载失败，请稍后重试')
        if (active) setData(result)
      } catch (failure) {
        if (active) setError(failure.name === 'AbortError' ? '短剧加载超时，请重试' : failure.message)
      } finally {
        clearTimeout(timer)
        if (active) setLoading(false)
      }
    }
    load()
    return () => { active = false; clearTimeout(timer); controller.abort() }
  }, [filter, attempt])
  function changePage(page) {
    window.scrollTo(0, 0)
    rememberModuleScroll('shortDramas')
    setFilter(previous => ({ ...previous, page }))
  }
  return <main className="movies-page">
    <AccountHeader />
    <form className="movie-search" role="search" onSubmit={event => {
      event.preventDefault(); applySearch(input.trim()); search.current?.blur()
    }}>
      <ModuleIcon name="search" />
      <input ref={search} type="search" aria-label="搜索短剧" placeholder="搜索短剧名称"
        value={input} maxLength={50} enterKeyHint="search" onChange={event => setInput(event.target.value)} />
      {input && <button type="button" aria-label="清空短剧搜索" onClick={() => { setInput(''); applySearch('') }}>✕</button>}
      <button type="submit">搜索</button>
    </form>
    <div className="movie-section-heading"><h1>{filter.query ? `“${filter.query}”的搜索结果` : '最新短剧'}</h1>
      <span>{loading ? '读取中…' : error ? '加载失败' : `共 ${data.total} 部合集`}</span></div>
    {loading ? <div className="movie-grid movie-skeleton" role="status" aria-label="正在加载短剧">
      {Array.from({ length: 6 }, (_, index) => <div className="movie-skeleton-card" key={index} />)}
    </div> : error ? <div className="movie-state" role="alert"><p>{error}</p>
      <button type="button" onClick={() => setAttempt(value => value + 1)}>重新加载短剧</button></div>
      : !data.items.length ? <div className="movie-state"><h2>{filter.query ? '没有找到相关短剧' : '暂无短剧合集'}</h2>
        <p>{filter.query ? '试试更短的片名。' : '授权短剧收录后会在这里展示。'}</p>
        {(filter.query || filter.page > 1) && <button type="button" onClick={() => {
          setInput(''); setFilter({ query: '', page: 1 })
        }}>查看最新短剧</button>}</div>
        : <div className="movie-grid">{data.items.map(item => <button type="button" className="movie-card"
          key={item.id} onClick={() => { rememberModuleScroll('shortDramas'); openMovie(item.url) }}>
          <div className="movie-poster"><Cover src={item.cover} label="短剧合集" />
            <span className="movie-card-badge">合集播放</span><span className="movie-card-play" aria-hidden="true">▷</span>
          </div><h2>{item.title}</h2><p>{durationText(item.duration) || '合集视频'} · Telegram 播放</p>
        </button>)}</div>}
    {!loading && !error && (filter.page > 1 || data.has_more) && <nav className="movie-categories" aria-label="短剧分页">
      <button type="button" disabled={filter.page === 1} onClick={() => changePage(filter.page - 1)}>上一页</button>
      <span className="movie-back">第 {filter.page} 页</span>
      <button type="button" disabled={!data.has_more} onClick={() => changePage(filter.page + 1)}>下一页</button>
    </nav>}
  </main>
}
