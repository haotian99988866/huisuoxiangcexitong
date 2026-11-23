import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import ModuleIcon from '../components/ModuleIcon'
import { LoadMore } from '../components/ContinuousList'
import { API_BASE_URL, hasTelegramBackButton } from '../utils/twa'
import { mediaUrl } from '../utils/tenant'
import { rememberModuleScroll } from '../utils/moduleState'
import { serviceFeeds } from '../utils/modules'
import { listingInfo, listingText } from '../utils/serviceListing'
import './ServiceFeedView.css'

const mediaSrc = item => mediaUrl(`${API_BASE_URL}${item.url}`)
const dateText = value => new Date(value).toLocaleDateString('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit',
})

function Arrow({ next = false }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
    <path d={next ? 'm9 5 7 7-7 7' : 'm15 5-7 7 7 7'} />
  </svg>
}

export default function ServiceFeedView({
  service, detailId, data, items, page, listPath, scrollKey, allowed, error,
  onBack, onRefresh, onOpenPhoto, fontSize, onFontSize, loadMore, Cover, Media, children,
}) {
  const vehicles = service.key === 'rentals'
  const kind = vehicles ? '车辆' : '房源'
  const info = data && detailId ? listingInfo(data, vehicles) : null
  return <main className={`sf-page${vehicles ? ' sf-vehicles' : ''}${detailId ? ' sf-detail' : ''}`}>
    <header className="sf-header">
      <div className="sf-toolbar">
        {!hasTelegramBackButton && <button className="sf-icon-button" aria-label={detailId ? `返回${kind}列表` : '返回服务中心'} onClick={onBack}><Arrow /></button>}
        <div className="sf-heading">
          <span className="sf-eyebrow">{vehicles ? '车辆展示' : '房源展示'} · 频道收录</span>
          {detailId ? <Link to={listPath}>{service.label}</Link> : <h1>{service.label}</h1>}
        </div>
        {detailId ? <button className="sf-icon-button sf-font" aria-label={`正文字号 ${fontSize}，点击切换`} onClick={onFontSize}>Aa</button>
          : <button className="sf-icon-button" aria-label="刷新服务信息" disabled={!allowed || (!data && !error)} onClick={onRefresh}>↻</button>}
      </div>
      {!detailId && <nav className="sf-tabs" aria-label="房源和车辆分类">
        {serviceFeeds.map(item => <Link key={item.key} to={item.path} aria-current={service.key === item.key ? 'page' : undefined}>
          <ModuleIcon name={item.icon} />{item.key === 'villas' ? '别墅' : item.key === 'apartments' ? '公寓' : '租车'}
        </Link>)}
      </nav>}
    </header>
    {!allowed ? <section className="sf-state" role="alert"><h2>此入口暂未开放该服务</h2></section>
      : error ? <section className="sf-state" role="alert"><h2>暂时无法加载</h2><p>{error}</p><button className="sf-primary" onClick={onRefresh}>重新加载</button></section>
        : !data ? <div className="sf-skeleton" role="status" aria-label={`正在加载${kind}`}>
          {[0, 1, 2].map(index => <div className="sf-skeleton-card" key={index}><span /><div><span /><span /><span /></div></div>)}
        </div>
          : detailId ? <article className="sf-detail-content">
            {data.media.length > 0 && <ServiceGallery key={data.id} media={data.media} kind={kind} Media={Media} onOpenPhoto={onOpenPhoto} />}
            <section className="sf-detail-summary">
              <div className="sf-detail-kicker"><span>{service.label}</span><time dateTime={data.published_at}>{dateText(data.published_at)} 发布</time></div>
              <h1>{info.title}</h1>
              {info.location && <p className="sf-location"><ModuleIcon name="location" />{info.location}</p>}
              <Price info={info} />
              <p className="sf-history-note">历史频道资料，不代表当前仍可租。</p>
            </section>
            {data.attributes?.length > 0 && <section className="sf-section">
              <h2>{vehicles ? '车辆信息' : '房源信息'}</h2>
              <dl className="sf-attributes">{data.attributes.map((field, index) =>
                <div key={`${field.label}:${index}`}><dt>{vehicles && field.label === '年龄' && /^(?:19|20)\d{2}年?$/.test(listingText(field.value)) ? '年份' : field.label}</dt>
                  <dd>{listingText(field.value)}</dd></div>)}</dl>
            </section>}
            {data.body && <section className="sf-section sf-description" style={{ '--sf-body-size': `${fontSize}px` }}>
              <h2>详细介绍</h2>{data.body.split(/\n\s*\n/).filter(Boolean).map((paragraph, index) => <p key={index}>{paragraph}</p>)}
            </section>}
            <Link className="sf-return" to={listPath}>返回{kind}列表<Arrow next /></Link>
          </article> : <>
            <div className="sf-list-meta"><strong>全部{kind}<span>{data.total} 条</span></strong><span>按发布时间更新</span></div>
            {!items.length ? <section className="sf-state"><ModuleIcon name={service.icon} /><h2>暂无{kind}资料</h2><p>频道消息收录后会显示在这里。</p></section>
              : <div className="sf-feed">{items.map(item => <ListingCard key={item.id} item={item} vehicles={vehicles}
                path={`${service.path}/${item.id}?page=${page}`} scrollKey={scrollKey} Cover={Cover} />)}</div>}
            {items.length > 0 && <LoadMore {...loadMore} />}
            <p className="sf-list-note">信息来自频道历史收录，价格及可租状态以实际核实为准。</p>
          </>}
    {children}
  </main>
}

function Price({ info }) {
  return <div className="sf-price">{info.price ? <><strong>{info.price}</strong><span>{info.priceLabel} · 原文报价</span></>
    : <span className="sf-price-missing">价格见介绍</span>}</div>
}

function ListingCard({ item, vehicles, path, scrollKey, Cover }) {
  const info = listingInfo(item, vehicles)
  return <Link className={`sf-card${!item.cover ? ' sf-card-no-media' : ''}`} to={path} onClick={() => rememberModuleScroll(scrollKey)}>
    {item.cover && <div className="sf-card-cover"><Cover item={item.cover} count={item.media_count} /></div>}
    <div className="sf-card-copy">
      <h2>{info.title}</h2>
      {info.location && <p className="sf-location"><ModuleIcon name="location" />{info.location}</p>}
      {info.features.length > 0 && <div className="sf-features">{info.features.slice(0, 3).map((field, index) => <span key={`${field.label}:${index}`}>{field.value}</span>)}</div>}
      {!item.attributes?.length && item.excerpt && <p className="sf-card-excerpt">{item.excerpt}</p>}
      <Price info={info} />
      <div className="sf-card-meta"><time dateTime={item.published_at}>{dateText(item.published_at)}</time>
        <span>{item.has_video ? '含视频' : item.media_count ? `${item.media_count} 图` : '文字介绍'}<Arrow next /></span></div>
    </div>
  </Link>
}

function ServiceGallery({ media, kind, Media, onOpenPhoto }) {
  const viewport = useRef(null)
  const [active, setActive] = useState(0)
  const activeRef = useRef(0)
  activeRef.current = active
  useEffect(() => {
    const node = viewport.current
    const observer = new ResizeObserver(() => node.scrollTo({ left: activeRef.current * node.clientWidth, behavior: 'auto' }))
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  const select = index => viewport.current.scrollTo({
    left: index * viewport.current.clientWidth,
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
  })
  return <section className="sf-gallery" aria-label={`${kind}相册`}>
    <div ref={viewport} className="sf-gallery-viewport" tabIndex={0}
      onKeyDown={event => {
        if (event.target !== event.currentTarget || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return
        event.preventDefault()
        select(Math.max(0, Math.min(media.length - 1, active + (event.key === 'ArrowLeft' ? -1 : 1))))
      }}
      onScroll={event => setActive(Math.max(0, Math.min(media.length - 1, Math.round(event.currentTarget.scrollLeft / Math.max(1, event.currentTarget.clientWidth)))))}>
      {media.map((item, index) => <div className="sf-gallery-slide" key={item.url} aria-hidden={active !== index}>
        <Media item={item} index={index} label={kind} lead active={active === index} onOpen={onOpenPhoto} />
      </div>)}
    </div>
    <span className="sf-gallery-count" aria-live="polite">{active + 1} / {media.length}</span>
    {media.length > 1 && <div className="sf-gallery-controls">
      <button className="sf-icon-button" disabled={active === 0} aria-label="上一项媒体" onClick={() => select(active - 1)}><Arrow /></button>
      <span>左右滑动查看 · 图片可放大</span>
      <button className="sf-icon-button" disabled={active === media.length - 1} aria-label="下一项媒体" onClick={() => select(active + 1)}><Arrow next /></button>
    </div>}
    {media.length > 1 && <div className="sf-thumbnails" aria-label="选择相册内容">{media.map((item, index) =>
      <button key={item.url} aria-label={`查看第 ${index + 1} 项${item.kind === 'video' ? '视频' : '图片'}`}
        aria-pressed={active === index} onClick={() => select(index)}>
        {item.kind === 'photo' ? <img src={mediaSrc(item)} alt="" loading="lazy" /> : <><ModuleIcon name="movies" /><span>视频 {index + 1}</span></>}
      </button>)}</div>}
  </section>
}
