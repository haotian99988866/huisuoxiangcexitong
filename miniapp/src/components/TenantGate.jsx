import { useEffect, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { API_BASE_URL, bindBackButton, closeMiniApp, trackVisit } from '../utils/twa'
import { getTenant, hasAnnouncementAccess, hasDeliveryAccess, hasVenueAccess, initializeTenant, setTenant, tenantHeaders, tenantPath } from '../utils/tenant'

export default function TenantGate({ children }) {
  const [state, setState] = useState({ loading: true, error: '' })
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    let controller
    let timer
    async function load() {
      controller = new AbortController()
      timer = setTimeout(() => controller.abort(), 15000)
      try {
        const slug = initializeTenant()
        if (slug === null) trackVisit()
        if (slug !== null) {
          const response = await fetch(`${API_BASE_URL}/webhook`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
            body: JSON.stringify({ action: 'tenant_config' }),
            signal: controller.signal,
          })
          const result = await response.json()
          if (!response.ok) throw new Error(result.error || '租户服务暂不可用')
          if (cancelled) return
          setTenant(result.tenant)
          document.title = result.tenant.brand_name
        }
        if (!cancelled) setState({ loading: false, error: '' })
      } catch (error) {
        if (cancelled) return
        console.error('[Tenant entry]', error)
        setState({ loading: false, error: error.name === 'AbortError' ? '加载超时，请重新打开专属入口' : error.message })
      } finally {
        clearTimeout(timer)
      }
    }
    load()
    const interval = setInterval(() => {
      if (getTenant()) load()
    }, 60000)
    return () => { cancelled = true; clearTimeout(timer); clearInterval(interval); controller?.abort() }
  }, [])

  useEffect(() => {
    if (!state.error) return undefined
    return bindBackButton(closeMiniApp)
  }, [state.error])

  useEffect(() => {
    if (state.loading || state.error || !getTenant()) return
    const scoped = tenantPath(`${location.pathname}${location.search}${location.hash}`)
    if (scoped !== `${location.pathname}${location.search}${location.hash}`) navigate(scoped, { replace: true })
  }, [state, location, navigate])

  if (state.loading) return <main className="container loading">正在加载服务…</main>
  if (state.error) return (
    <main className="container">
      <h1>服务暂不可用</h1>
      <p role="alert">{state.error}</p>
      <button type="button" onClick={() => window.location.reload()}>重新加载</button>
    </main>
  )
  const config = getTenant()
  const module = new URLSearchParams(location.search).get('module')
  const announcementPage = /^\/announcements(?:\/\d+)?$/.test(location.pathname) && hasAnnouncementAccess()
  if (config && ((!['/', '/movies'].includes(location.pathname) && !announcementPage) ||
      (location.pathname === '/' && (module === 'delivery' ? !hasDeliveryAccess() : !hasVenueAccess())))) {
    return <Navigate to={tenantPath(hasVenueAccess() ? '/?module=venue' : '/?module=delivery')} replace />
  }
  return children
}
