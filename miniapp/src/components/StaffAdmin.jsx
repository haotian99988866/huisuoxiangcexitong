import { useState } from 'react'
import StaffGallery from './StaffGallery'
import DeliveryStaffAdmin from './DeliveryStaffAdmin'

export default function StaffAdmin({ venues }) {
  const available = venues.filter(venue => [1, 2, 4, 5, 6, 7, 8, 9, 10].includes(venue.id))
  const [selectedId, setSelectedId] = useState(available[0]?.id)
  const selected = available.find(venue => venue.id === selectedId)
  const delivery = [7, 8, 9, 10].includes(selected?.id)
  return <section>
    <select aria-label="选择会所" className="staff-admin-select" value={selectedId || ''}
      onChange={event => setSelectedId(Number(event.target.value))}>
      {available.map(venue => <option key={venue.id} value={venue.id}>{venue.name}</option>)}
    </select>
    {selected ? delivery ? <DeliveryStaffAdmin key={selected.id} venue={selected} archive /> :
      <StaffGallery key={selected.id} venue={selected} admin /> : <p>暂无可管理会所。</p>}
  </section>
}
