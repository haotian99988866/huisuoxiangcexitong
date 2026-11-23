import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import ModuleIcon from './ModuleIcon'
import { useMusic } from './MusicPlayer'
import { navigationSection } from '../utils/commerce'
import { subscribeFullscreen } from '../utils/twa'
import './AppNavigation.css'

const GROUP_DESTINATIONS = {
  mingxi: { id: 2, label: '名玺', icon: 'mingxi', ids: ['1', '2', '3'] },
  dalang: { id: 4, label: '大浪', icon: 'dalang', ids: ['4'] },
  wuxiang: { id: 5, label: '五象', icon: 'wuxiang', ids: ['5'] },
  dihao: { id: 6, label: '帝豪', icon: 'dihao', ids: ['6'] },
}

export default function AppNavigation({ children }) {
  const location = useLocation()
  const player = useMusic()
  const section = navigationSection(location.pathname)
  const [fullscreen, setFullscreen] = useState(false)
  const lastVenue = useRef(2)
  useEffect(() => subscribeFullscreen(setFullscreen), [])
  useEffect(() => {
    if (location.pathname === '/') {
      const venue = new URLSearchParams(location.search).get('venue')
      if (GROUP_DESTINATIONS.mingxi.ids.includes(String(venue))) {
        lastVenue.current = Number(venue)
      }
    }
  }, [location, section])
  const currentVenue = new URLSearchParams(location.search).get('venue') || (
    section === 'home' ? String(lastVenue.current) : ''
  )
  const deliveryActive = new URLSearchParams(location.search).get('module') === 'delivery'
  const venueUrl = group => `/?module=venue&venue=${group === GROUP_DESTINATIONS.mingxi ? lastVenue.current : group.id}`
  const checkout = ['/membership', '/phone-topup'].includes(location.pathname)
  return <div className={`app-shell${fullscreen ? ' app-shell-fullscreen' : ''}`}>
    {children}
    {!checkout && !player?.expanded && <nav className="app-bottom-nav" aria-label="主导航">
      {[
        ['mingxi', GROUP_DESTINATIONS.mingxi.label, GROUP_DESTINATIONS.mingxi.icon,
          venueUrl(GROUP_DESTINATIONS.mingxi),
          section === 'home' && !deliveryActive && GROUP_DESTINATIONS.mingxi.ids.includes(String(currentVenue))],
        ['dalang', GROUP_DESTINATIONS.dalang.label, GROUP_DESTINATIONS.dalang.icon,
          venueUrl(GROUP_DESTINATIONS.dalang),
          section === 'home' && !deliveryActive && GROUP_DESTINATIONS.dalang.ids.includes(String(currentVenue))],
        ['wuxiang', GROUP_DESTINATIONS.wuxiang.label, GROUP_DESTINATIONS.wuxiang.icon,
          venueUrl(GROUP_DESTINATIONS.wuxiang),
          section === 'home' && !deliveryActive && GROUP_DESTINATIONS.wuxiang.ids.includes(String(currentVenue))],
        ['dihao', GROUP_DESTINATIONS.dihao.label, GROUP_DESTINATIONS.dihao.icon,
          venueUrl(GROUP_DESTINATIONS.dihao),
          section === 'home' && !deliveryActive && GROUP_DESTINATIONS.dihao.ids.includes(String(currentVenue))],
        ['delivery', '外送', 'delivery', '/?module=delivery',
          section === 'home' && deliveryActive],
        ['personal', '我的', 'personal', '/personal', section !== 'home'],
      ].map(([key, label, icon, path, active]) => <Link key={key} to={path}
        aria-current={active ? 'page' : undefined}
        onClick={event => {
          if (new URL(path, window.location.origin).pathname === location.pathname && active) {
            event.preventDefault()
            window.scrollTo({ top: 0, behavior: 'smooth' })
          }
        }}>
        <ModuleIcon name={icon} /><span>{label}</span>
      </Link>)}
    </nav>}
  </div>
}
