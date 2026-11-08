import { useEffect, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { API_BASE_URL } from '../utils/twa'
import { mediaUrl } from '../utils/tenant'
import './StaffGallery.css'

export default function DeliveryPersonPicker({ venueId, selectedId, onSelect }) {
  const [people, setPeople] = useState(null)
  const [error, setError] = useState('')
  const [failedImages, setFailedImages] = useState(new Set())

  useEffect(() => {
    let active = true
    setPeople(null)
    setError('')
    requestAdmin('admin_delivery_staff', { venue_id: Number(venueId) })
      .then(result => {
        if (active) setPeople(result.people.filter(person => !person.merged_into_id))
      })
      .catch(failure => {
        console.error('[Delivery person picker]', failure)
        if (active) setError(failure.message)
      })
    return () => { active = false }
  }, [venueId])

  return <div className="staff-gallery delivery-person-picker" aria-label="外送人员档案相册">
    <h3>选择外送人员档案</h3>
    <button type="button" className="staff-skip" onClick={() => onSelect(null)}>
      暂不关联人员，后续可补录
    </button>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {!people && !error && <p role="status">正在读取外送人员相册…</p>}
    {people && !people.length && <p className="admin-hint">当前来源暂无可选外送人员档案。</p>}
    {!!people?.length && <div className="staff-grid">
      {people.map(person => {
        const cover = person.media.find(item => item.media_type === 'photo')
        return <article className="staff-card" key={person.id}>
          <div className="staff-cover">
            {cover && !failedImages.has(cover.id)
              ? <img loading="lazy" src={mediaUrl(`${API_BASE_URL}/delivery-media/${cover.id}?thumb=1`)}
                alt={`${person.display_name || '外送人员'}照片`}
                onError={() => setFailedImages(current => new Set([...current, cover.id]))} />
              : <span>{cover ? '照片暂不可用' : '暂无照片'}</span>}
          </div>
          <div className="staff-card-info">
            <strong>{person.display_name || '外送人员'}</strong>
            <small>{person.media_count} 张照片/视频</small>
          </div>
          <button type="button" className="staff-replace" aria-pressed={selectedId === person.id}
            onClick={() => onSelect(person)}>
            {selectedId === person.id ? '已选择' : '选择此人员'}
          </button>
        </article>
      })}
    </div>}
  </div>
}
