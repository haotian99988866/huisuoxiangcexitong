import { useEffect, useRef, useState } from 'react'

// 进入可视区附近才开始加载；失败显示占位，不当成功淡入
export default function LazyMedia({ as: Tag = 'img', src, style, ...rest }) {
  const nodeRef = useRef(null)
  const [inView, setInView] = useState(typeof IntersectionObserver === 'undefined')
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (inView) return undefined
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setInView(true)
        observer.disconnect()
      }
    }, { rootMargin: '300px' })
    observer.observe(nodeRef.current)
    return () => observer.disconnect()
  }, [inView])

  useEffect(() => {
    setLoaded(false)
    setFailed(false)
  }, [src])

  const loadProps = Tag === 'video'
    ? { onLoadedData: () => setLoaded(true) }
    : { onLoad: () => setLoaded(true) }

  if (failed) {
    return (
      <div
        ref={nodeRef}
        role="img"
        aria-label="媒体加载失败"
        style={{
          ...style,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(0,0,0,0.06)',
          color: '#8a7a6a',
          fontSize: 12,
        }}
      >
        加载失败
      </div>
    )
  }

  return (
    <Tag
      ref={nodeRef}
      {...rest}
      {...(inView ? { src, ...loadProps, onError: () => setFailed(true) } : {})}
      style={{ ...style, opacity: loaded ? 1 : 0, transition: 'opacity 0.35s ease' }}
    />
  )
}