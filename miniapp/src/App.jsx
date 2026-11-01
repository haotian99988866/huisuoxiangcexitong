import { useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import VenueList from './pages/VenueList'
import VenueDetail from './pages/VenueDetail'
import TechnicianList from './pages/TechnicianList'
import MovieList from './pages/MovieList'
import Recharge from './pages/Recharge'
import Membership from './pages/Membership'
import Personal from './pages/Personal'
import Admin from './pages/Admin'
import Music from './pages/Music'
import Announcements from './pages/Announcements'
import Services from './pages/Services'
import Orders from './pages/Orders'
import WalletHistory from './pages/WalletHistory'
import AppNavigation from './components/AppNavigation'
import MusicPlayer from './components/MusicPlayer'
import { isTenantEntry } from './utils/tenant'
import { modules } from './utils/modules'
import TenantGate from './components/TenantGate'
import SeasonalWelcome from './components/SeasonalWelcome'

// 机器人按钮带 startapp=movies 时直接进入观影页
function StartParamRedirect() {
  const navigate = useNavigate()
  useEffect(() => {
    const param = window.Telegram?.WebApp?.initDataUnsafe?.start_param
    if (/^playlist_[0-9a-f]{32}$/.test(param || '')) {
      navigate(`/music?playlist=${param.slice(9)}`, { replace: true })
      return
    }
    const target = modules.find(item => item.startParam === param && param)
    if (target) navigate(target.path, { replace: true })
  }, [navigate])
  return null
}

export default function App() {
  return (
    <Router>
      <TenantGate>
        <Content />
      </TenantGate>
    </Router>
  )
}

function Content() {
  const routes = (
    <>
      <StartParamRedirect />
      <Routes>
        <Route path="/" element={<VenueList />} />
        <Route path="/movies" element={<MovieList />} />
        <Route path="/media" element={<Navigate to="/movies" replace />} />
        <Route path="/recharge" element={<Recharge />} />
        <Route path="/membership" element={<Membership />} />
        <Route path="/personal" element={<Personal />} />
        <Route path="/services" element={<Services />} />
        <Route path="/orders" element={<Orders />} />
        <Route path="/orders/:orderId" element={<Orders />} />
        <Route path="/wallet-history" element={<WalletHistory />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/music" element={<Music />} />
        <Route path="/announcements" element={<Announcements />} />
        <Route path="/announcements/:id" element={<Announcements />} />
        <Route path="/venue/:id" element={<VenueDetail />} />
        <Route path="/venue/:venue_id/technicians" element={<TechnicianList />} />
      </Routes>
    </>
  )
  return <div className="ink-app">
    <SeasonalWelcome background />
    {isTenantEntry() ? routes : <MusicPlayer><AppNavigation>{routes}</AppNavigation></MusicPlayer>}
  </div>
}