import { useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, useNavigate } from 'react-router-dom'
import VenueList from './pages/VenueList'
import VenueDetail from './pages/VenueDetail'
import TechnicianList from './pages/TechnicianList'
import MovieList from './pages/MovieList'
import Recharge from './pages/Recharge'
import Personal from './pages/Personal'
import Admin from './pages/Admin'
import { modules } from './utils/modules'
import TenantGate from './components/TenantGate'

// 机器人按钮带 startapp=movies 时直接进入观影页
function StartParamRedirect() {
  const navigate = useNavigate()
  useEffect(() => {
    const param = window.Telegram?.WebApp?.initDataUnsafe?.start_param
    const target = modules.find(item => item.startParam === param && param)
    if (target) navigate(target.path, { replace: true })
  }, [navigate])
  return null
}

export default function App() {
  return (
    <Router>
      <TenantGate>
        <StartParamRedirect />
        <Routes>
          <Route path="/" element={<VenueList />} />
          <Route path="/movies" element={<MovieList />} />
          <Route path="/recharge" element={<Recharge />} />
          <Route path="/personal" element={<Personal />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/venue/:id" element={<VenueDetail />} />
          <Route path="/venue/:venue_id/technicians" element={<TechnicianList />} />
        </Routes>
      </TenantGate>
    </Router>
  )
}