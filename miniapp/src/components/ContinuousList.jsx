import { useEffect, useRef } from 'react'
import './ContinuousList.css'

export function LoadMore({ hasMore, busy = false, error = '', onLoad, disabled = false, root = null }) {
  const ref = useRef(null)
  const callback = useRef(onLoad)
  callback.current = onLoad
  useEffect(() => {
    if (!hasMore || busy || error || disabled) return
    let requested = false
    const loadIfNearEnd = () => {
      if (requested || !ref.current) return
      const bounds = ref.current.getBoundingClientRect()
      const viewportBottom = root ? root.getBoundingClientRect().bottom : window.innerHeight
      if (bounds.top <= viewportBottom + 160) {
        requested = true
        callback.current()
      }
    }
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) loadIfNearEnd()
    }, { root, rootMargin: '160px' })
    observer?.observe(ref.current)
    const scrollTarget = root || window
    scrollTarget.addEventListener('scroll', loadIfNearEnd, { passive: true })
    loadIfNearEnd()
    return () => {
      observer?.disconnect()
      scrollTarget.removeEventListener('scroll', loadIfNearEnd)
    }
  }, [hasMore, busy, error, disabled, root])
  return <div ref={ref} className="continuous-load" aria-live="polite">
    {error ? <><p role="alert">{error}</p><button type="button" disabled={busy || disabled} onClick={onLoad}>重试加载更多</button></>
      : busy ? <span role="status">正在加载…</span>
        : hasMore ? <button type="button" disabled={disabled} onClick={onLoad}>加载更多</button>
          : <span>已全部展示</span>}
  </div>
}
