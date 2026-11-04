import { useEffect, useRef, useState } from 'react'
import { pullDistance } from '../utils/continuousList'
import './ContinuousList.css'

export function LoadMore({ hasMore, busy = false, error = '', onLoad, disabled = false, root = null }) {
  const ref = useRef(null)
  const callback = useRef(onLoad)
  callback.current = onLoad
  useEffect(() => {
    if (!hasMore || busy || error || disabled || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) callback.current()
    }, { root, rootMargin: '160px' })
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [hasMore, busy, error, disabled, root])
  return <div ref={ref} className="continuous-load" aria-live="polite">
    {error ? <><p role="alert">{error}</p><button type="button" disabled={busy || disabled} onClick={onLoad}>重试加载更多</button></>
      : busy ? <span role="status">正在加载…</span>
        : hasMore ? <button type="button" disabled={disabled} onClick={onLoad}>加载更多</button>
          : <span>已全部展示</span>}
  </div>
}

export function ListRefresh({ onRefresh, busy = false, disabled = false }) {
  const ref = useRef(null)
  const state = useRef({ onRefresh, busy, disabled })
  state.current = { onRefresh, busy, disabled }
  const [distance, setDistance] = useState(0)
  useEffect(() => {
    const root = ref.current.closest('main')
    if (!root) return
    let start = null
    let pulled = 0
    const reset = () => { start = null; pulled = 0; setDistance(0) }
    const touchStart = event => {
      reset()
      if (state.current.busy || state.current.disabled || window.scrollY > 1 || event.touches.length !== 1
        || event.target.closest('input,textarea,select,a,dialog,[role="dialog"]')
        || event.target.closest('button:not(.movie-card):not(.music-song-button)')) return
      start = { x: event.touches[0].clientX, y: event.touches[0].clientY }
    }
    const touchMove = event => {
      if (!start || event.touches.length !== 1) { reset(); return }
      pulled = pullDistance(start, { x: event.touches[0].clientX, y: event.touches[0].clientY })
      if (pulled > 4 && event.cancelable) event.preventDefault()
      setDistance(pulled)
    }
    const touchEnd = () => {
      const refresh = pulled >= 72 && !state.current.busy && !state.current.disabled
      reset()
      if (refresh) state.current.onRefresh()
    }
    root.addEventListener('touchstart', touchStart, { passive: true })
    root.addEventListener('touchmove', touchMove, { passive: false })
    root.addEventListener('touchend', touchEnd)
    root.addEventListener('touchcancel', reset)
    const tg = window.Telegram?.WebApp
    const swipes = tg?.isVerticalSwipesEnabled
    if (tg?.isVersionAtLeast?.('7.7') && swipes) tg.disableVerticalSwipes()
    return () => {
      root.removeEventListener('touchstart', touchStart)
      root.removeEventListener('touchmove', touchMove)
      root.removeEventListener('touchend', touchEnd)
      root.removeEventListener('touchcancel', reset)
      if (tg?.isVersionAtLeast?.('7.7') && swipes) tg.enableVerticalSwipes()
    }
  }, [])
  return <div ref={ref} className="continuous-refresh">
    <span role="status">{busy ? '正在刷新…' : distance >= 72 ? '松开刷新' : distance ? '继续下拉刷新' : '下拉刷新最新内容'}</span>
    <button type="button" onClick={onRefresh} disabled={busy || disabled}>刷新</button>
  </div>
}
