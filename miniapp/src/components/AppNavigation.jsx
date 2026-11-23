import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import ModuleIcon from './ModuleIcon'
import { useMusic } from './MusicPlayer'
import { navigationSection } from '../utils/commerce'
import { subscribeFullscreen } from '../utils/twa'
import './AppNavigation.css'

const GROUP_DESTINATIONS = {
  mingxi: { id: 2, label: '名玺', icon: 'mingxi', ids: ['1', '2', '3'] },
  venue: { id: 4, label: '会所', icon: 'venue', ids: ['4', '5', '6'] },
}

export default function AppNavigation({ children }) {
  const location = useLocation()
  const player = useMusic()
  const section = navigationSection(location.pathname)
  const [fullscreen, setFullscreen] = useState(false)
  const lastVenue = useRef(2)
  const lastClubVenue = useRef(4)
  useEffect(() => subscribeFullscreen(setFullscreen), [])
  useEffect(() => {
    if (location.pathname === '/') {
      const venue = new URLSearchParams(location.search).get('venue')
      if (GROUP_DESTINATIONS.mingxi.ids.includes(String(venue))) {
        lastVenue.current = Number(venue)
      } else if (GROUP_DESTINATIONS.venue.ids.includes(String(venue))) {
        lastClubVenue.current = Number(venue)
      }
    }
  }, [location, section])
  const currentVenue = new URLSearchParams(location.search).get('venue') || (
    section === 'home' ? String(lastVenue.current) : ''
  )
  const deliveryActive = new URLSearchParams(location.search).get('module') === 'delivery'
  const venueUrl = group => `/?module=venue&venue=${
    group === GROUP_DESTINATIONS.mingxi ? lastVenue.current
      : group === GROUP_DESTINATIONS.venue ? lastClubVenue.current : group.id
  }`
  const checkout = ['/membership', '/phone-topup'].includes(location.pathname)
  return <div className={`app-shell${fullscreen ? ' app-shell-fullscreen' : ''}`}>
    {children}
    {!checkout && !player?.expanded && <nav className="app-bottom-nav" aria-label="主导航">
      {[
        ['mingxi', GROUP_DESTINATIONS.mingxi.label, GROUP_DESTINATIONS.mingxi.icon,
          venueUrl(GROUP_DESTINATIONS.mingxi),
          section === 'home' && !deliveryActive && GROUP_DESTINATIONS.mingxi.ids.includes(String(currentVenue))],
        ['venue', GROUP_DESTINATIONS.venue.label, GROUP_DESTINATIONS.venue.icon,
          venueUrl(GROUP_DESTINATIONS.venue),
          section === 'home' && !deliveryActive && GROUP_DESTINATIONS.venue.ids.includes(String(currentVenue))],
        ['delivery', '外送', 'delivery', '/?module=delivery',
          section === 'home' && deliveryActive],
        ['services', '服务', 'services', '/services', location.pathname === '/services'],
        ['personal', '我的', 'personal', '/personal',
          section !== 'home' && location.pathname !== '/services'],
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
