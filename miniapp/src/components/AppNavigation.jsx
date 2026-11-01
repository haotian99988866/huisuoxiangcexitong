import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import ModuleIcon from './ModuleIcon'
import { useMusic } from './MusicPlayer'
import { navigationSection } from '../utils/commerce'
import { subscribeFullscreen } from '../utils/twa'
import './AppNavigation.css'

export default function AppNavigation({ children }) {
  const location = useLocation()
  const player = useMusic()
  const section = navigationSection(location.pathname)
  const [fullscreen, setFullscreen] = useState(false)
  const homePath = useRef('/')
  useEffect(() => subscribeFullscreen(setFullscreen), [])
  useEffect(() => {
    if (location.pathname === '/') {
      homePath.current = `${location.pathname}${location.search}`
    }
  }, [location, section])
  const checkout = location.pathname === '/membership'
  return <div className={`app-shell${fullscreen ? ' app-shell-fullscreen' : ''}`}>
    {children}
    {!checkout && !player?.expanded && <nav className="app-bottom-nav" aria-label="主导航">
      {[
        ['home', '会所/外送', 'venue', homePath.current],
        ['movies', '观影', 'movies', '/movies'],
        ['music', '音乐', 'music', '/music'],
        ['services', '服务', 'membership', '/services'],
        ['personal', '我的', 'personal', '/personal'],
      ].map(([key, label, icon, path]) => <Link key={key} to={path}
        aria-current={section === key ? 'page' : undefined}
        onClick={event => { if (`${location.pathname}${location.search}` === path) event.preventDefault() }}>
        <ModuleIcon name={icon} /><span>{label}</span>
      </Link>)}
    </nav>}
  </div>
}
