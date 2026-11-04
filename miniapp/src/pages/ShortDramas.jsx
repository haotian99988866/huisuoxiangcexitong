import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import AccountHeader from '../components/AccountHeader'
import ModuleIcon from '../components/ModuleIcon'
import { Cover, durationText, openMovie } from './MovieList'
import { rememberModuleScroll, useModuleScroll, useModuleState } from '../utils/moduleState'
import { bindBackButton } from '../utils/twa'
import usePagedCatalog from '../utils/usePagedCatalog'
import { ListRefresh, LoadMore } from '../components/ContinuousList'
import './MovieList.css'
import './ShortDramas.css'

const itemKey = item => item.id

export default function ShortDramas() {
  const navigate = useNavigate()
  const [input, setInput] = useModuleState('shortDramas.input', '')
  const [filter, setFilter] = useModuleState('shortDramas.filter', { query: '' })
  const catalog = usePagedCatalog('shortDramas',
    { action: 'get_short_dramas', query: filter.query }, 'items', itemKey)
  const { loading, error, refresh } = catalog
  const data = catalog.data || { items: [], total: 0, has_more: false }
  const search = useRef(null)
  useModuleScroll('shortDramas', catalog.ready)
  useEffect(() => bindBackButton(() => navigate('/services')), [navigate])
  const applySearch = value => setFilter(previous => previous.query === value
    ? previous : { query: value })
  useEffect(() => {
    const timer = setTimeout(() => setFilter(previous => previous.query === input.trim()
      ? previous : { query: input.trim() }), 350)
    return () => clearTimeout(timer)
  }, [input, setFilter])
  return <main className="movies-page short-dramas-page">
    <AccountHeader>
      <form className="movie-search" role="search" onSubmit={event => {
      event.preventDefault(); applySearch(input.trim()); search.current?.blur()
    }}>
      <ModuleIcon name="search" />
      <input ref={search} type="search" aria-label="搜索短剧" placeholder="搜索短剧名称"
        value={input} maxLength={50} enterKeyHint="search" onChange={event => setInput(event.target.value)} />
      {input && <button type="button" aria-label="清空短剧搜索" onClick={() => { setInput(''); applySearch('') }}>✕</button>}
      <button type="submit">搜索</button>
      </form>
    </AccountHeader>
    <ListRefresh onRefresh={refresh} busy={catalog.refreshing} disabled={catalog.busy} />
    <div className="movie-section-heading"><h1>{filter.query ? `“${filter.query}”的搜索结果` : '最新短剧'}</h1>
      <span>{loading ? '读取中…' : error ? '加载失败' : `共 ${data.total} 部合集`}</span></div>
    {loading ? <div className="movie-grid movie-skeleton" role="status" aria-label="正在加载短剧">
      {Array.from({ length: 6 }, (_, index) => <div className="movie-skeleton-card" key={index} />)}
    </div> : error && !catalog.ready ? <div className="movie-state" role="alert"><p>{error}</p>
      <button type="button" onClick={refresh}>重新加载短剧</button></div>
      : !data.items.length ? <div className="movie-state"><h2>{filter.query ? '没有找到相关短剧' : '暂无短剧合集'}</h2>
        <p>{filter.query ? '试试更短的片名。' : '授权短剧收录后会在这里展示。'}</p>
        {filter.query && <button type="button" onClick={() => {
          setInput(''); setFilter({ query: '' })
        }}>查看最新短剧</button>}</div>
        : <div className="movie-grid">{data.items.map(item => <button type="button" className="movie-card"
          key={item.id} onClick={() => { rememberModuleScroll('shortDramas'); openMovie(item.url) }}>
          <div className="movie-poster"><Cover src={item.cover} label="短剧合集" />
            <span className="movie-card-badge">合集播放</span><span className="movie-card-play" aria-hidden="true">▷</span>
          </div><h2>{item.title}</h2><p>{durationText(item.duration) || '合集视频'} · Telegram 播放</p>
        </button>)}</div>}
    {catalog.ready && <LoadMore hasMore={data.has_more} busy={catalog.busy}
      error={error} onLoad={catalog.loadMore} />}
  </main>
}
