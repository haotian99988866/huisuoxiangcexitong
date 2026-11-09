import { useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from '../utils/twa'
import { contactName, contactUrl, mediaUrl } from '../utils/tenant'
import { bookingUrl } from '../utils/booking'
import { recommendationKinds, requestRecommendations } from '../utils/venueRecommendations'
import MusicDialog from './MusicDialog'
import CopyBooking from './CopyBooking'
import './VenueRecommendations.css'

function RecommendationVideo({ src, active, autoPlayAllowed, onEnded, onDimensions, onOpen, title, tabIndex }) {
  const video = useRef(null)
  const [playError, setPlayError] = useState('')
  const [mediaError, setMediaError] = useState(false)
  const [playing, setPlaying] = useState(false)
  const start = () => {
    setPlayError('')
    return video.current.play().catch(failure => {
      if (failure.name === 'AbortError') return
      console.error('[Recommendation video play]', failure)
      setPlayError('自动播放受限，请点击播放')
    })
  }
  useEffect(() => {
    const element = video.current
    let current = true
    if (active && autoPlayAllowed) {
      if (element.ended) element.currentTime = 0
      element.play().catch(failure => {
        if (!current || failure.name === 'AbortError') return
        console.error('[Recommendation video autoplay]', failure)
        setPlayError('自动播放受限，请点击播放')
      })
    } else element.pause()
    return () => { current = false; element.pause() }
  }, [active, autoPlayAllowed, src])
  return <div className="venue-recommendation-video">
    <button type="button" className="venue-recommendation-photo" onClick={onOpen}
      tabIndex={tabIndex} aria-label={`查看推荐说明：${title}`}>
    <video ref={video} src={src} muted playsInline disablePictureInPicture disableRemotePlayback
      controlsList="nodownload nofullscreen noremoteplayback" preload={active ? 'auto' : 'metadata'}
      aria-label={title} onLoadedMetadata={event => onDimensions(event.currentTarget.videoWidth, event.currentTarget.videoHeight)}
      onPlay={event => { if (!active) event.currentTarget.pause(); else { setPlayError(''); setPlaying(true) } }}
      onPause={() => setPlaying(false)}
      onEnded={() => { if (active) onEnded() }}
      onError={() => { setMediaError(true); console.error('[Recommendation video] Media unavailable', src) }} />
    </button>
    {mediaError ? <p role="alert">视频暂不可用，请切换下一条或稍后重试。</p>
      : active && (playError || (!autoPlayAllowed && !playing)) &&
        <button type="button" className="venue-recommendation-play" onClick={start}>{playError || '点击播放'}</button>}
  </div>
}

export default function VenueRecommendations({ venueId }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [index, setIndex] = useState(0)
  const [now, setNow] = useState(Date.now())
  const [clockOffset, setClockOffset] = useState(0)
  const [selectedId, setSelectedId] = useState(null)
  const [paused, setPaused] = useState(false)
  const [reducedMotion, setReducedMotion] = useState(false)
  const [failedImages, setFailedImages] = useState(new Set())
  const [dimensions, setDimensions] = useState({})
  const [railWidth, setRailWidth] = useState(0)
  const [visible, setVisible] = useState(false)
  const [foreground, setForeground] = useState(document.visibilityState === 'visible')
  const rail = useRef(null)
  const section = useRef(null)
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    requestRecommendations(venueId, controller.signal).then(result => {
      if (!active) return
      setClockOffset(Date.parse(result.server_now) - Date.now())
      setNow(Date.now()); setData(result); setError(''); setFailedImages(new Set())
    }).catch(failure => {
      if (!active) return
      console.error('[Venue recommendations]', failure)
      setData(null)
      setError(failure.name === 'AbortError' ? '推荐读取超时' : failure.message)
    }).finally(() => clearTimeout(timer))
    return () => { active = false; clearTimeout(timer); controller.abort() }
  }, [venueId, reload])
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000)
    const refresh = () => {
      setForeground(document.visibilityState === 'visible')
      if (document.visibilityState === 'visible') setReload(value => value + 1)
    }
    const poll = setInterval(refresh, 30000)
    document.addEventListener('visibilitychange', refresh)
    return () => { clearInterval(tick); clearInterval(poll); document.removeEventListener('visibilitychange', refresh) }
  }, [])
  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReducedMotion(media.matches)
    update(); media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  const items = (data?.recommendations || []).filter(item =>
    item.enabled && Date.parse(item.starts_at) <= now + clockOffset && Date.parse(item.ends_at) > now + clockOffset)
  const activeIndex = Math.min(index, Math.max(0, items.length - 1))
  const selected = items.find(item => item.id === selectedId)
  const hasItems = items.length > 0
  useEffect(() => {
    if (!hasItems || !rail.current || !section.current) return
    setRailWidth(rail.current.clientWidth)
    const resize = new ResizeObserver(() => setRailWidth(rail.current?.clientWidth || 0))
    resize.observe(rail.current)
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: 0.25 })
    observer.observe(section.current)
    return () => { resize.disconnect(); observer.disconnect() }
  }, [hasItems])
  useEffect(() => {
    if (selectedId && !selected) setSelectedId(null)
  }, [selectedId, selected])
  const itemIds = items.map(item => item.id).join(',')
  const ratio = item => dimensions[`${item.id}-${item.revision}`] || (
    item.media_width && item.media_height ? item.media_width / item.media_height : 1)
  const cardWidth = item => Math.min(railWidth, Math.max(180, 260 * ratio(item)))
  const recordDimensions = (item, width, height) => {
    if (!width || !height) return
    const key = `${item.id}-${item.revision}`
    setDimensions(current => current[key] === width / height ? current : { ...current, [key]: width / height })
  }
  const move = next => {
    const element = rail.current
    const card = element?.querySelectorAll('.venue-recommendation-slide')[next]
    if (card) element.scrollTo({
      left: element.scrollLeft + card.getBoundingClientRect().left - element.getBoundingClientRect().left - (element.clientWidth - card.clientWidth) / 2,
      behavior: reducedMotion ? 'instant' : 'smooth',
    })
    setIndex(next)
  }
  const layoutKey = items.map(item => `${item.id}:${cardWidth(item)}`).join(',')
  useEffect(() => {
    const element = rail.current
    const card = element?.querySelectorAll('.venue-recommendation-slide')[activeIndex]
    if (card) element.scrollLeft += card.getBoundingClientRect().left - element.getBoundingClientRect().left - (element.clientWidth - card.clientWidth) / 2
  }, [layoutKey])
  useEffect(() => { setIndex(0) }, [itemIds])
  const videoActive = visible && foreground && !selected
  const next = () => { if (items.length > 1) move((activeIndex + 1) % items.length) }
  useEffect(() => {
    if (items.length < 2 || paused || !videoActive || reducedMotion || items[activeIndex]?.media_type === 'video') return
    const timer = setTimeout(next, 7000)
    return () => clearTimeout(timer)
  }, [activeIndex, items.length, paused, videoActive, reducedMotion, items[activeIndex]?.media_type, itemIds])
  const image = item => mediaUrl(`${API_BASE_URL}${item.image_url}`)
  const contact = selected ? bookingUrl({
    title: `你好，我想咨询：${selected.title}`,
    links: [new URL(image(selected), window.location.origin).href],
    details: [
      `${recommendationKinds[selected.kind]} · 有效至 ${new Date(selected.ends_at).toLocaleString('zh-CN', { hour12: false })}`,
      selected.description,
    ],
  }) : ''
  const failImage = id => setFailedImages(current => new Set([...current, id]))
  if (error) return <div className="venue-recommendation-error" role="alert">{error}
    <button type="button" onClick={() => setReload(value => value + 1)}>重试推荐</button></div>
  if (!items.length) return null
  return <section ref={section} className="venue-recommendations" aria-label="名玺精选推荐" aria-roledescription="轮播"
    onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
    onFocus={() => setPaused(true)} onBlur={event => {
      if (!event.currentTarget.contains(event.relatedTarget)) setPaused(false)
    }}>
    <div className="venue-recommendation-rail" ref={rail} style={railWidth ? {
      paddingLeft: (railWidth - cardWidth(items[0])) / 2, paddingRight: (railWidth - cardWidth(items.at(-1))) / 2,
    } : undefined}
      onScroll={event => {
        const element = event.currentTarget, center = element.getBoundingClientRect().left + element.clientWidth / 2
        const distances = [...element.querySelectorAll('.venue-recommendation-slide')].map(card =>
          Math.abs(card.getBoundingClientRect().left + card.clientWidth / 2 - center))
        if (distances.length) setIndex(distances.indexOf(Math.min(...distances)))
      }}
      onTouchStart={() => setPaused(true)} onTouchEnd={() => setPaused(false)} onTouchCancel={() => setPaused(false)}>
      {items.map((item, position) => <article key={`${item.id}-${item.revision}`} className="venue-recommendation-slide"
        style={{ width: railWidth ? cardWidth(item) : '100%', '--media-ratio': ratio(item) }}>
        {item.media_type === 'video'
          ? <RecommendationVideo src={image(item)} title={item.title} active={position === activeIndex && videoActive}
            autoPlayAllowed={!reducedMotion}
            onOpen={() => setSelectedId(item.id)} tabIndex={position === activeIndex ? 0 : -1}
            onEnded={next} onDimensions={(width, height) => recordDimensions(item, width, height)} />
          : <button type="button" className="venue-recommendation-photo"
            aria-label={`${recommendationKinds[item.kind]}：${item.title}`} tabIndex={position === activeIndex ? 0 : -1}
            onClick={() => setSelectedId(item.id)}>
            {failedImages.has(item.id) ? <span className="venue-recommendation-image-error">推荐图暂不可用，点击查看说明</span>
              : <img src={image(item)} alt={item.title} loading={position ? 'lazy' : 'eager'}
                onLoad={event => recordDimensions(item, event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)}
                onError={() => failImage(item.id)} />}
          </button>}
        <button type="button" className="venue-recommendation-caption" onClick={() => setSelectedId(item.id)}
          tabIndex={position === activeIndex ? 0 : -1} aria-label={`查看推荐说明：${item.title}`}>
          <small>{recommendationKinds[item.kind]}</small><strong>{item.title}</strong>
        </button>
      </article>)}
    </div>
    {selected && <MusicDialog className="venue-recommendation-sheet" title={selected.title} scrollBody onClose={() => setSelectedId(null)}
      footer={<div className="venue-recommendation-contact-panel">
        <p>联系{contactName()} · Telegram：{new URL(contactUrl()).pathname.replace('/', '@')}</p>
        <CopyBooking key={selected.id} url={contact} openChat />
      </div>}>
      {failedImages.has(selected.id) ? <p role="alert">推荐图暂不可用，请稍后重试。</p>
        : selected.media_type === 'video' ? <video src={image(selected)} muted playsInline controls aria-label={selected.title}
          onError={() => failImage(selected.id)} />
          : <img src={image(selected)} alt={selected.title} onError={() => failImage(selected.id)} />}
      <p className="staff-meta">{recommendationKinds[selected.kind]} · 有效至 {new Date(selected.ends_at).toLocaleString('zh-CN', { hour12: false })}</p>
      <p className="venue-recommendation-description">{selected.description}</p>
    </MusicDialog>}
  </section>
}
