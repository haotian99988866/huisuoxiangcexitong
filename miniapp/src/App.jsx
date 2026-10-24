import { useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, useNavigate } from 'react-router-dom'
import VenueList from './pages/VenueList'
import VenueDetail from './pages/VenueDetail'
import TechnicianList from './pages/TechnicianList'
import MovieList from './pages/MovieList'

// 机器人按钮带 startapp=movies 时直接进入观影页
function StartParamRedirect() {
  const navigate = useNavigate()
  useEffect(() => {
    const param = window.Telegram?.WebApp?.initDataUnsafe?.start_param
    if (param === 'movies') navigate('/movies', { replace: true })
  }, [navigate])
  return null
}

export default function App() {
  return (
    <Router>
      <StartParamRedirect />
      <Routes>
        <Route path="/" element={<VenueList />} />
        <Route path="/movies" element={<MovieList />} />
        <Route path="/venue/:id" element={<VenueDetail />} />
        <Route path="/venue/:venue_id/technicians" element={<TechnicianList />} />
      </Routes>
    </Router>
  )
}