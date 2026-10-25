import { useEffect, useLayoutEffect, useState } from 'react'

const values = new Map()
const positions = new Map()

export function rememberModuleScroll(key) {
  positions.set(key, window.scrollY)
}

// Only retain browsing preferences for this page session, never account data.
export function useModuleState(key, initialValue) {
  const [value, setValue] = useState(() => values.has(key) ? values.get(key) : initialValue)
  useEffect(() => { values.set(key, value) }, [key, value])
  return [value, setValue]
}

export function useModuleScroll(key, ready) {
  useLayoutEffect(() => {
    if (!ready) {
      window.scrollTo(0, 0)
      return
    }
    window.scrollTo(0, positions.get(key) || 0)
    const onScroll = () => rememberModuleScroll(key)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
    }
  }, [key, ready])
}
