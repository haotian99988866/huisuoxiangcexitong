import { useState } from 'react'
import StaffGallery from './StaffGallery'
import StaffManagement from './StaffManagement'

export default function StaffAdmin({ venues }) {
  const available = venues.filter(venue => [1, 2, 4, 5, 6].includes(venue.id))
  const [selectedId, setSelectedId] = useState(available[0]?.id)
  const [archive, setArchive] = useState(false)
  const selected = available.find(venue => venue.id === selectedId)
  return <section>
    <label>选择会所<select className="staff-admin-select" value={selectedId || ''}
      onChange={event => setSelectedId(Number(event.target.value))}>
      {available.map(venue => <option key={venue.id} value={venue.id}>{venue.name}</option>)}
    </select></label>
    <nav className="crm-tabs" aria-label="人员管理视图">
      <button type="button" aria-pressed={!archive} onClick={() => setArchive(false)}>人员概览</button>
      <button type="button" aria-pressed={archive} onClick={() => setArchive(true)}>档案维护</button>
    </nav>
    <p className="admin-hint">概览用于内部标签与出勤订阅；工号换人请到档案维护。外送独立管理。</p>
    {selected ? archive ? <StaffGallery key={selected.id} venue={selected} admin /> :
      <StaffManagement key={selected.id} venue={selected} /> : <p>暂无可管理会所。</p>}
  </section>
}
