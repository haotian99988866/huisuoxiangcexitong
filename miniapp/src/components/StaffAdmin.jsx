import { useState } from 'react'
import StaffGallery from './StaffGallery'

export default function StaffAdmin({ venues }) {
  const available = venues.filter(venue => [1, 2, 4, 5, 6].includes(venue.id))
  const [selectedId, setSelectedId] = useState(available[0]?.id)
  const selected = available.find(venue => venue.id === selectedId)
  return <section>
    <label>选择会所<select className="staff-admin-select" value={selectedId || ''}
      onChange={event => setSelectedId(Number(event.target.value))}>
      {available.map(venue => <option key={venue.id} value={venue.id}>{venue.name}</option>)}
    </select></label>
    <p className="admin-hint">档案自动积累，相同照片不重复保存。工号换人时先确认新人，再发布出勤照片；外送独立管理。</p>
    {selected ? <StaffGallery key={selected.id} venue={selected} admin /> : <p>暂无可管理会所。</p>}
  </section>
}
