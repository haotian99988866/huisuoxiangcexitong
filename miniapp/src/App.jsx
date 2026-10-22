import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import VenueList from './pages/VenueList'
import VenueDetail from './pages/VenueDetail'

export default function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<VenueList />} />
        <Route path="/venue/:id" element={<VenueDetail />} />
      </Routes>
    </Router>
  )
}