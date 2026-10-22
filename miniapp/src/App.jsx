import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import VenueList from './pages/VenueList'
import VenueDetail from './pages/VenueDetail'
import TechnicianList from './pages/TechnicianList'

export default function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<VenueList />} />
        <Route path="/venue/:id" element={<VenueDetail />} />
        <Route path="/venue/:venue_id/technicians" element={<TechnicianList />} />
      </Routes>
    </Router>
  )
}