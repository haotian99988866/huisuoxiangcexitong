import { lazy, Suspense, useEffect, useRef } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import VenueList from './pages/VenueList'
import VenueDetail from './pages/VenueDetail'
import TechnicianList from './pages/TechnicianList'
import MovieList from './pages/MovieList'
import ShortDramas from './pages/ShortDramas'
import Recharge from './pages/Recharge'
import Membership from './pages/Membership'
import PhoneTopup from './pages/PhoneTopup'
import Personal from './pages/Personal'
import PersonalFinance from './pages/PersonalFinance'
import Admin from './pages/Admin'
import Music from './pages/Music'
import Announcements from './pages/Announcements'
import Services from './pages/Services'
import Subscriptions from './pages/Subscriptions'
import Hotels from './pages/Hotels'
import Orders from './pages/Orders'
import WalletHistory from './pages/WalletHistory'
import Consumption from './pages/Consumption'
import ContactProfileNotice from './components/ContactProfileNotice'
import AppNavigation from './components/AppNavigation'
import MusicPlayer from './components/MusicPlayer'
import { isTenantEntry } from './utils/tenant'
import { modules } from './utils/modules'
import TenantGate from './components/TenantGate'
import AnalyticsTracker from './components/AnalyticsTracker'
import './App.css'

const FinancePreview = lazy(() => import('./pages/FinancePreview'))

// 机器人按钮带 startapp=movies 时直接进入观影页
function StartParamRedirect() {
  const navigate = useNavigate()
  const applied = useRef(false)
  useEffect(() => {
    if (applied.current) return
    applied.current = true
    const param = window.Telegram?.WebApp?.initDataUnsafe?.start_param
    if (/^customer_-?[1-9]\d{0,15}$/.test(param || '') && Number.isSafeInteger(Number(param.slice(9)))) {
      navigate(`/admin?customer=${param.slice(9)}`, { replace: true })
      return
    }
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
  const location = useLocation()
  const isPersonalFinance = location.pathname === '/personal-finance'
  if (isPersonalFinance && new URLSearchParams(location.search).get('preview') === 'layout') {
    return <Suspense fallback={<p role="status">正在加载账房预览…</p>}><FinancePreview /></Suspense>
  }
  const routes = (
    <>
      <StartParamRedirect />
      <Routes>
        <Route path="/" element={<VenueList />} />
        <Route path="/movies" element={<MovieList />} />
        <Route path="/short-dramas" element={<ShortDramas />} />
        <Route path="/media" element={<Navigate to="/movies" replace />} />
        <Route path="/recharge" element={<Recharge />} />
        <Route path="/membership" element={<Membership />} />
        <Route path="/phone-topup" element={<PhoneTopup />} />
        <Route path="/personal" element={<Personal />} />
        <Route path="/personal-finance" element={<PersonalFinance />} />
        <Route path="/services" element={<Services />} />
        <Route path="/subscriptions" element={<Subscriptions />} />
        <Route path="/hotels" element={<Hotels />} />
        <Route path="/orders" element={<Orders />} />
        <Route path="/orders/:orderId" element={<Orders />} />
        <Route path="/wallet-history" element={<WalletHistory />} />
        <Route path="/consumption" element={<Consumption />} />
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
    <AnalyticsTracker />
    <ContactProfileNotice />
    {isTenantEntry() ? routes : <MusicPlayer>
      {isPersonalFinance ? routes : <AppNavigation>{routes}</AppNavigation>}
    </MusicPlayer>}
  </div>
}