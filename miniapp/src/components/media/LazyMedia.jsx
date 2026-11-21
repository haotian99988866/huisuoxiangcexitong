import { useEffect, useRef, useState } from 'react'

/**
 * 进视口才加载；加载中保留骨架底，失败可点重试。
 * 不再用 opacity:0 整块透明，避免列表「闪空白」。
 */
export default function LazyMedia({ as: Tag = 'img', src, style, className = '', onError, ...rest }) {
  const nodeRef = useRef(null)
  const [inView, setInView] = useState(typeof IntersectionObserver === 'undefined')
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    if (inView) return undefined
    const node = nodeRef.current
    if (!node) return undefined
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setInView(true)
        observer.disconnect()
      }
    }, { rootMargin: '300px' })
    observer.observe(node)
    return () => observer.disconnect()
  }, [inView])

  useEffect(() => {
    setLoaded(false)
    setFailed(false)
  }, [src, retry])

  const loadProps = Tag === 'video'
    ? { onLoadedData: () => setLoaded(true) }
    : { onLoad: () => setLoaded(true) }

  const shellStyle = {
    ...style,
    position: style?.position || 'relative',
    background: loaded ? (style?.background || 'transparent') : 'linear-gradient(110deg, #efe8de 25%, #f6f1e8 40%, #efe8de 55%)',
    backgroundSize: loaded ? undefined : '200% 100%',
  }

  if (failed) {
    return (
      <button
        type="button"
        ref={nodeRef}
        className={className}
        aria-label="媒体加载失败，点击重试"
        onClick={() => setRetry(value => value + 1)}
        style={{
          ...shellStyle,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexDirection: 'column',
          gap: 6,
          border: 0,
          padding: 0,
          margin: 0,
          color: '#8a7a6a',
          fontSize: 12,
          font: 'inherit',
          cursor: 'pointer',
          background: 'rgba(0,0,0,0.06)',
        }}
      >
        <span>加载失败</span>
        <span style={{ color: 'var(--ink-accent, #6b4f3a)', fontWeight: 600 }}>点击重试</span>
      </button>
    )
  }

  const resolvedSrc = inView && src
    ? (retry ? `${src}${src.includes('?') ? '&' : '?'}r=${retry}` : src)
    : undefined

  return (
    <Tag
      ref={nodeRef}
      className={className}
      {...rest}
      {...(resolvedSrc ? {
        src: resolvedSrc,
        ...loadProps,
        onError: event => {
          setFailed(true)
          onError?.(event)
        },
      } : {})}
      style={{
        ...shellStyle,
        opacity: loaded ? 1 : 0.35,
        transition: 'opacity 0.25s ease',
      }}
    />
  )
}
