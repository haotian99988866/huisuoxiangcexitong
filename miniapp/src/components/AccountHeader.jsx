import { useEffect, useState } from 'react'
import { subscribeFullscreen } from '../utils/twa'
import { AnnouncementCard, FullscreenAnnouncementBar } from './Announcement'
import './AccountHeader.css'

export default function AccountHeader({ children }) {
  const [fullscreen, setFullscreen] = useState(() => Boolean(window.Telegram?.WebApp?.isFullscreen))
  useEffect(() => subscribeFullscreen(setFullscreen), [])
  return <div className={`account-module-header${fullscreen ? ' account-module-header-fullscreen' : ''}`}>
    {fullscreen ? <FullscreenAnnouncementBar /> : <AnnouncementCard />}
    {children}
  </div>
}
