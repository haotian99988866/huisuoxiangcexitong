import { useEffect, useState } from 'react'
import { API_BASE_URL } from '../utils/twa'
import { mediaUrl, tenantHeaders } from '../utils/tenant'
import MusicDialog from './MusicDialog'
import CopyBooking from './CopyBooking'
import { bookingUrl } from '../utils/booking'
import './StaffGallery.css'

export default function DeliveryArchive({ venue }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(new Set())
  const [selected, setSelected] = useState(null)
  const [failedMedia, setFailedMedia] = useState(new Set())
  const contact = selected ? bookingUrl({
    title: `你好，我想预约${venue.categoryLabel || venue.name}`,
    details: [`档案 #${selected.id}`],
    links: selected.media.map(item =>
      new URL(mediaUrl(`${API_BASE_URL}/delivery-media/${item.id}`), window.location.origin).href),
  }) : ''
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    const timeout = setTimeout(() => controller.abort(), 20000)
    setError('')
    fetch(`${API_BASE_URL}/webhook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
      body: JSON.stringify({
        action: 'get_delivery_archive', venue_id: venue.id,
        category: venue.categoryKey === '10-small' ? 'small' :
          venue.categoryKey === '10-red' ? 'red' : null,
      }),
      cache: 'no-store', signal: controller.signal,
    }).then(async response => {
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || '外送人员档案读取失败')
      if (active) setData(result)
    }).catch(failure => {
      if (!active) return
      console.error('[Delivery archive]', failure)
      setError(failure.name === 'AbortError' ? '读取超时，请重试' : failure.message)
    }).finally(() => clearTimeout(timeout))
    return () => { active = false; controller.abort(); clearTimeout(timeout) }
  }, [venue.id, venue.categoryKey, attempt])
  return <section className="staff-gallery" aria-label="外送人员档案">
    <div className="staff-heading">
      <h2>人员档案</h2>
      <button type="button" onClick={() => setAttempt(value => value + 1)}>刷新</button>
    </div>
    {error && <p role="alert" className="membership-error">{error}</p>}
    {!data && !error && <p role="status">正在读取人员档案…</p>}
    {data && <>
      <p className="staff-meta">全部人员 · {data.people.length} 份档案 · {data.shift_date} {data.shift}。
        未见照片不代表实际缺勤。</p>
      {!data.people.length && <p className="staff-meta">暂无可展示的人员档案。</p>}
      <div className="staff-grid">
        {data.people.map(person => {
          const cover = person.media.find(item => item.type === 'photo')
          return <article className="staff-card" key={person.id}>
            <button type="button" className="staff-open" onClick={() => {
              setSelected(person)
              setFailedMedia(new Set())
            }}>
              <div className="staff-cover">
                {cover && !failed.has(cover.id)
                  ? <img loading="lazy" alt={`档案 #${person.id}`}
                    src={mediaUrl(`${API_BASE_URL}/delivery-media/${cover.id}?thumb=1`)}
                    onError={() => setFailed(current => new Set([...current, cover.id]))} />
                  : <span>{cover ? '照片暂不可用' : '视频档案 · 点击查看'}</span>}
                <span className={`staff-attendance-overlay${person.on_duty ? ' is-on-duty' : ''}`}>
                  {person.attendance_overlay}
                </span>
              </div>
              <div className="staff-card-info">
                <strong>档案 #{person.id}</strong>
                <small>{person.media.length} 条照片/视频</small>
              </div>
            </button>
          </article>
        })}
      </div>
      {selected && <MusicDialog title={`档案 #${selected.id}`} className="account-sheet staff-sheet"
        scrollBody onClose={() => setSelected(null)}
        footer={<><CopyBooking url={contact} /><a className="account-primary-link" href={contact}
          target="_blank" rel="noreferrer">联系预约</a></>}>
        <p className="staff-meta">{selected.attendance_overlay}</p>
        {selected.media.map(item => <div key={item.id}>
          {failedMedia.has(item.id) ? <p role="alert">媒体加载失败，请关闭后重试。</p> :
            item.type === 'video' ? <video className="staff-detail-photo" controls playsInline preload="none"
              src={mediaUrl(`${API_BASE_URL}/delivery-media/${item.id}`)}
              onError={() => setFailedMedia(current => new Set([...current, item.id]))} /> :
              <img className="staff-detail-photo" loading="lazy" alt={`档案 #${selected.id}`}
                src={mediaUrl(`${API_BASE_URL}/delivery-media/${item.id}`)}
                onError={() => setFailedMedia(current => new Set([...current, item.id]))} />}
        </div>)}
      </MusicDialog>}
    </>}
  </section>
}
