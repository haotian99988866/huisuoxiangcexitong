import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import ModuleTabs from '../components/ModuleTabs'
import { LoadMore } from '../components/ContinuousList'
import { FullscreenAnnouncementBar } from '../components/Announcement'
import { API_BASE_URL, bindBackButton, hasTelegramBackButton, openTelegramLink, registerBackOverlay, subscribeFullscreen } from '../utils/twa'
import { getTenant, hasAnnouncementAccess, mediaUrl, tenantPath } from '../utils/tenant'
import { rememberModuleScroll, useModuleScroll, useModuleState } from '../utils/moduleState'
import { requestAnnouncements } from '../utils/announcements'
import './Announcements.css'

const dateText = value => new Date(value).toLocaleString('zh-CN', {
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
})
const fullDateText = value => new Date(value).toLocaleString('zh-CN', {
  year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
})
const imageUrl = item => mediaUrl(`${API_BASE_URL}${item.url}`)

function NewsIcon({ name, ...props }) {
  const paths = {
    back: 'm15 18-6-6 6-6',
    next: 'm9 18 6-6-6-6',
    close: 'm6 6 12 12M6 18 18 6',
    refresh: 'M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3a7 7 0 0 0 11.6-1',
    image: 'M4 4h16v16H4zM4 16l5-5 4 4 3-3 4 4M8 8h.01',
    map: 'M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15m6-12v15',
    play: 'm8 5 12 7-12 7z',
    up: 'm6 14 6-6 6 6',
  }
  return <svg {...props} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>
}

export default function Announcements() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const pageValue = Number(params.get('page'))
  const requestedPage = Number.isInteger(pageValue) && pageValue > 0 && pageValue <= 100000 ? pageValue : 1
  const historyPath = `/announcements?page=${requestedPage}`
  const requestKey = id ? `detail:${id}` : `list:${requestedPage}`
  const scrollKey = `announcements:list:${requestedPage}`
  const [loaded, setLoaded] = useState({ key: '', data: null, error: '' })
  const [moreItems, setMoreItems] = useState([])
  const [moreState, setMoreState] = useState({ busy: false, error: '' })
  const moreRequest = useRef(null)
  const result = loaded.key === requestKey ? loaded.data : null
  const error = loaded.key === requestKey ? loaded.error : ''
  const feedItems = result && !id ? [...result.items, ...moreItems] : []
  const hasMore = Boolean(result && result.page < result.pages)
  const [attempt, setAttempt] = useState(0)
  const [fullscreen, setFullscreen] = useState(() => Boolean(window.Telegram?.WebApp?.isFullscreen))
  const [fontSize, setFontSize] = useModuleState('announcements.fontSize', 18)
  const [viewer, setViewer] = useState(null)
  const allowed = hasAnnouncementAccess()
  const goBack = () => navigate(tenantPath(id ? historyPath : '/'))
  useEffect(() => subscribeFullscreen(setFullscreen), [])
  useEffect(() => bindBackButton(() => navigate(tenantPath(id ? historyPath : '/'))), [navigate, id, historyPath])
  useModuleScroll(scrollKey, Boolean(result) && !id)
  useLayoutEffect(() => {
    if (id) window.scrollTo(0, 0)
    setViewer(null)
  }, [id])
  useEffect(() => {
    if (!allowed) return
    const controller = new AbortController()
    let active = true
    moreRequest.current?.abort()
    moreRequest.current = null
    setMoreItems([])
    setMoreState({ busy: false, error: '' })
    setLoaded({ key: requestKey, data: null, error: '' })
    const timer = setTimeout(() => controller.abort(), 15000)
    requestAnnouncements(id ? 'get_announcement' : 'get_announcements',
      id ? { id: Number(id) } : { page: requestedPage }, controller.signal)
      .then(value => { if (active) setLoaded({ key: requestKey, data: value, error: '' }) }).catch(failure => {
        if (!active) return
        console.error('[Announcements]', failure)
        setLoaded({ key: requestKey, data: null,
          error: failure.name === 'AbortError' ? '加载超时，请检查网络后重试' : failure.message })
      }).finally(() => clearTimeout(timer))
    return () => {
      active = false
      clearTimeout(timer)
      controller.abort()
      moreRequest.current?.abort()
      moreRequest.current = null
    }
  }, [id, requestedPage, requestKey, allowed, attempt])
  const loadMore = async () => {
    if (!result || id || !hasMore || moreRequest.current) return
    const controller = new AbortController()
    moreRequest.current = controller
    setMoreState({ busy: true, error: '' })
    try {
      const page = result.page + 1
      const next = await requestAnnouncements('get_announcements', { page }, controller.signal)
      if (controller.signal.aborted) return
      setMoreItems(items => [...items, ...next.items])
      setLoaded(current => current.key === requestKey && current.data
        ? { ...current, data: { ...current.data, page: next.page, pages: next.pages, total: next.total } }
        : current)
    } catch (failure) {
      if (controller.signal.aborted) return
      console.error('[Announcements load more]', failure)
      setMoreState({ busy: false, error: failure.message })
      return
    } finally {
      if (moreRequest.current === controller) moreRequest.current = null
    }
    setMoreState({ busy: false, error: '' })
  }
  const photos = result?.media?.filter(item => item.kind === 'photo') || []
  const openPhoto = item => setViewer(photos.indexOf(item))
  return <main className={`announcements-page${id ? ' news-reading' : ''}${fullscreen ? ' news-fullscreen' : ''}`}>
    <div className="news-sticky">
      {fullscreen && (id ? <div className="news-telegram-clearance" /> : <FullscreenAnnouncementBar />)}
      {!id && <ModuleTabs active="announcements" />}
      <div className="news-toolbar">
        {id && !hasTelegramBackButton && <button className="news-icon-button" aria-label="返回资讯列表" onClick={goBack}><NewsIcon name="back" /></button>}
        {id ? <Link className="news-toolbar-title" to={tenantPath(historyPath)}>{getTenant()?.brand_name || '东南亚大事件'}</Link>
          : <div className="news-list-heading"><h1>{fullscreen ? '最新资讯' : '东南亚大事件'}</h1><span>{result ? `${result.total} 条` : '按时间更新'}</span></div>}
        {id ? <div className="news-reading-actions">
          <Link className="news-all-link" to={tenantPath(historyPath)}>全部资讯</Link>
          <button className="news-font-button" onClick={() => setFontSize(value => value === 20 ? 16 : value + 2)}
            aria-label={`正文字号 ${fontSize}，点击切换`}><span>Aa</span><small>字号</small></button>
        </div>
          : <button className="news-icon-button" aria-label="刷新资讯" disabled={!allowed || (!result && !error)}
            onClick={() => setAttempt(value => value + 1)}><NewsIcon name="refresh" /></button>}
      </div>
      {id && <ReadingProgress articleId={id} ready={Boolean(result)} />}
    </div>
    {allowed && <div className="news-subscription">
      <span>新资讯，机器人及时提醒<span>主动订阅 · 随时取消</span></span>
      <button type="button" onClick={() => openTelegramLink(`https://t.me/haotianzhaopian_bot?start=news${getTenant()?.slug ? `_${getTenant().slug}` : ''}`)}>订阅 / 管理</button>
    </div>}
    {!allowed ? <div className="news-state" role="alert"><h2>此入口暂未开放资讯</h2><p>请联系服务提供方了解详情。</p></div>
      : error ? <div className="news-state" role="alert"><h2>暂时无法加载</h2><p>{error}</p><button className="news-primary-button" onClick={() => setAttempt(value => value + 1)}>重新加载</button></div>
        : !result ? <NewsSkeleton detail={Boolean(id)} />
          : id ? <>
            <article className="news-article" style={{ '--news-body-size': `${fontSize}px` }}>
              <header className="news-article-heading">
                <span className="news-category">{result.category}</span>
                <h1>{result.title}</h1>
                <div className="news-byline"><span className="news-source-mark" aria-hidden="true">讯</span><div><strong>频道资讯</strong><time dateTime={result.published_at}>{fullDateText(result.published_at)}</time></div></div>
              </header>
              {result.media.length > 0 && <section className="news-attachments news-media-at-top" aria-label="相关图片与视频">
                <div className="news-section-label"><span>相关影像</span><small>{result.media.length} 项 · 点击图片查看大图</small></div>
                <div className={`news-media-grid${result.media.length === 1 ? ' news-media-grid-single' : ''}`}>{result.media.map((item, index) =>
                  <AnnouncementMedia key={`${id}:${index}`} item={item} index={index} onOpen={openPhoto} />)}</div>
              </section>}
              {result.maps?.length > 0 && <section className="news-map-section" aria-label="地图位置">
                <div className="news-section-label"><span>地图位置</span><small>{result.maps.length} 个位置</small></div>
                <div className="news-map-list">{result.maps.map((map, index) =>
                  <NewsMapPreview key={`${map.url}:${index}`} item={map} index={index} />)}</div>
              </section>}
              {result.content && <div className="news-article-body">{result.content.split(/\n\s*\n/).filter(Boolean).map((paragraph, index) => <p key={index}>{paragraph}</p>)}</div>}
              <div className="news-article-end"><span />全文完<span /></div>
            </article>
            <footer className="news-reading-footer"><div><span className="news-eyebrow">继续发现</span><h2>看看更多频道动态</h2><p>回到资讯列表，接着上次的位置浏览。</p></div>
              <button className="news-text-button" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}><NewsIcon name="up" />回到顶部</button>
            </footer>
          </> : <>
            {!feedItems.length ? <div className="news-state"><NewsIcon name="image" /><h2>新的动态，敬请期待</h2><p>频道消息收录后，会出现在这里。</p></div>
              : <div className="news-feed">{feedItems.map((item, index) => <Link className={`news-feed-item${!item.cover ? ' news-text-only' : ''}`} key={item.id}
                onClick={() => rememberModuleScroll(scrollKey)} to={tenantPath(`/announcements/${item.id}?page=${requestedPage}`)}>
                <div className="news-feed-copy">
                  <div className="news-feed-kicker">{result.page === 1 && index === 0 && <span className="news-latest">最新</span>}<span>{item.category}</span></div>
                  <h3>{item.title}</h3>
                  {item.excerpt && <p>{item.excerpt}</p>}
                  {item.maps?.length > 0 && <NewsMapPreview item={item.maps[0]} compact />}
                  <div className="news-feed-meta"><time dateTime={item.published_at}>{dateText(item.published_at)}</time><span>{item.has_video ? '含视频' : item.media_count ? `${item.media_count} 图` : '文字'}</span></div>
                </div>
                {item.cover && <NewsCover key={item.cover.url} item={item.cover} count={item.media_count} />}
              </Link>)}</div>}
            {feedItems.length > 0 && <LoadMore hasMore={hasMore} busy={moreState.busy}
              error={moreState.error} onLoad={loadMore} />}
          </>}
    {viewer !== null && <PhotoViewer key={id} photos={photos} initialIndex={viewer} onClose={() => setViewer(null)} />}
  </main>
}

function ReadingProgress({ articleId, ready }) {
  const bar = useRef(null)
  useEffect(() => {
    const update = () => {
      const article = document.querySelector('.news-article')
      if (!article || !bar.current) return
      const end = article.getBoundingClientRect().bottom + window.scrollY - window.innerHeight
      const progress = end > 0 ? Math.min(1, Math.max(0, window.scrollY / end)) : 1
      bar.current.style.transform = `scaleX(${progress})`
    }
    update()
    window.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update)
    const observer = new ResizeObserver(update)
    const article = document.querySelector('.news-article')
    if (article) observer.observe(article)
    return () => { observer.disconnect(); window.removeEventListener('scroll', update); window.removeEventListener('resize', update) }
  }, [articleId, ready])
  return <div className="news-reading-progress" aria-hidden="true"><span ref={bar} /></div>
}

function NewsCover({ item, count }) {
  const [failed, setFailed] = useState(false)
  const [nearby, setNearby] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (item.kind !== 'video') return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        setNearby(true)
        observer.disconnect()
      }
    }, { rootMargin: '160px' })
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [item.kind])
  return <div className="news-cover" ref={ref}>
    {failed ? <NewsIcon name="image" /> : item.kind === 'video'
      ? <video src={nearby ? imageUrl(item) : undefined} muted playsInline preload={nearby ? 'metadata' : 'none'} aria-hidden="true" onError={() => setFailed(true)} />
      : <img src={imageUrl(item)} alt="" loading="lazy" onError={() => setFailed(true)} />}
    {!failed && item.kind === 'video' && <span className="news-cover-play"><NewsIcon name="play" /></span>}
    {count > 1 && <span className="news-cover-count"><NewsIcon name="image" />{count}</span>}
    {failed && <span className="news-cover-unavailable">预览暂不可用</span>}
  </div>
}

function NewsMapPreview({ item, index = 0, compact = false }) {
  return <div className={`news-map-preview${compact ? ' news-map-preview-compact' : ''}`}>
    {item.preview_url
      ? <iframe src={item.preview_url} loading="lazy" referrerPolicy="no-referrer"
        title={`地图位置预览 ${index + 1}`} />
      : <div className="news-map-fallback"><NewsIcon name="map" /><span>地图位置</span></div>}
    {compact
      ? <span className="news-map-caption"><NewsIcon name="map" />含地图位置</span>
      : <a href={item.url} target="_blank" rel="noopener noreferrer">打开地图</a>}
  </div>
}

function NewsSkeleton({ detail }) {
  return <div className={`news-skeleton${detail ? ' news-skeleton-detail' : ''}`} role="status" aria-label="正在加载资讯">
    {[0, 1, 2].map(index => <div key={index}><span /><span /><span /></div>)}
  </div>
}

function AnnouncementMedia({ item, index, onOpen, lead = false }) {
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  return <figure className={`news-media${lead ? ' news-media-lead' : ''}${item.kind === 'video' ? ' news-media-video' : ''}`}>
    {failed ? <figcaption role="alert"><NewsIcon name="image" /><span>媒体加载失败</span><button className="news-text-button" onClick={() => { setFailed(false); setRetry(value => value + 1) }}>点击重试</button></figcaption>
      : item.kind === 'video' ? <video key={retry} controls playsInline preload="metadata" src={imageUrl(item)} aria-label={`资讯视频 ${index + 1}`} onError={() => setFailed(true)} />
        : <button className="news-photo-button" onClick={() => onOpen(item)} aria-label={`查看图片 ${index + 1} 大图`}>
          <img key={retry} src={imageUrl(item)} alt={`资讯图片 ${index + 1}`} loading={lead ? 'eager' : 'lazy'} onError={() => setFailed(true)} />
          <span>{lead ? '点击查看大图' : String(index + 1).padStart(2, '0')}</span>
        </button>}
  </figure>
}

function PhotoViewer({ photos, initialIndex, onClose }) {
  const dialogRef = useRef(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const touch = useRef(null)
  const [index, setIndex] = useState(initialIndex)
  const [zoomed, setZoomed] = useState(false)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const frameRef = useRef(null)
  useEffect(() => {
    const dialog = dialogRef.current
    const focus = document.activeElement
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.showModal()
    const unbind = registerBackOverlay(() => closeRef.current())
    return () => {
      unbind()
      dialog.close()
      document.body.style.overflow = overflow
      if (focus instanceof HTMLElement && focus.isConnected) focus.focus({ preventScroll: true })
    }
  }, [])
  const change = next => {
    setIndex(Math.max(0, Math.min(photos.length - 1, next)))
    setZoomed(false)
    setFailed(false)
    setRetry(0)
    frameRef.current?.scrollTo(0, 0)
  }
  return <dialog ref={dialogRef} className="news-photo-viewer" aria-label="图片查看器"
    onCancel={event => { event.preventDefault(); onClose() }}
    onClose={() => closeRef.current()}
    onKeyDown={event => {
      if (event.key === 'ArrowLeft') { event.preventDefault(); change(index - 1) }
      if (event.key === 'ArrowRight') { event.preventDefault(); change(index + 1) }
    }}>
    <header><span aria-live="polite">{index + 1}<small> / {photos.length}</small></span><button className="news-icon-button" aria-label="关闭图片查看器" onClick={onClose}><NewsIcon name="close" /></button></header>
    <div ref={frameRef} className={`news-photo-stage${zoomed ? ' is-zoomed' : ''}`}
      onTouchStart={event => { touch.current = event.touches.length === 1 ? { x: event.touches[0].clientX, y: event.touches[0].clientY } : null }}
      onTouchEnd={event => {
        if (!touch.current || zoomed) return
        const dx = event.changedTouches[0].clientX - touch.current.x
        const dy = event.changedTouches[0].clientY - touch.current.y
        if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) change(index + (dx < 0 ? 1 : -1))
        touch.current = null
      }}>
      {failed ? <div className="news-viewer-error" role="alert"><p>图片暂时无法加载</p><button className="news-text-button" onClick={() => { setFailed(false); setRetry(value => value + 1) }}>重新加载</button></div>
        : <img key={`${index}:${retry}`} src={imageUrl(photos[index])} alt={`大图 ${index + 1}`} onError={() => setFailed(true)} />}
    </div>
    <footer>
      <button className="news-icon-button" disabled={index === 0} aria-label="上一张图片" onClick={() => change(index - 1)}><NewsIcon name="back" /></button>
      <button className="news-text-button" disabled={failed} aria-pressed={zoomed} onClick={() => setZoomed(value => !value)}>{zoomed ? '还原图片' : '放大查看'}</button>
      <button className="news-icon-button" disabled={index === photos.length - 1} aria-label="下一张图片" onClick={() => change(index + 1)}><NewsIcon name="next" /></button>
    </footer>
  </dialog>
}
