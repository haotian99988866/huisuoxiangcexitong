import { useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from '../utils/twa'
import { contactName, mediaUrl, tenantHeaders } from '../utils/tenant'
import MusicDialog from './MusicDialog'
import CopyBooking from './CopyBooking'
import { bookingUrl } from '../utils/booking'
import useCustomerSubscriptions from '../utils/useCustomerSubscriptions'
import { SubscriptionControl, SubscriptionFeedback } from './CustomerDutyControls'
import './StaffGallery.css'

export default function DeliveryArchive({ venue, photoFirst = false, attendanceOnly = false }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(new Set())
  const [selected, setSelected] = useState(null)
  const [failedMedia, setFailedMedia] = useState(new Set())
  const [mediaIndex, setMediaIndex] = useState(0)
  const touchStart = useRef(null)
  const subscription = useCustomerSubscriptions(venue.id, () => setAttempt(value => value + 1), { delivery: true })
  const dutyLabel = person => {
    const state = subscription.states?.[person.id]
    return state ? state.published_at ? '今晚出勤' : state.shift === '晚班' ? '本班未见照片' : '外送仅晚班'
      : person.attendance_overlay
  }
  const contact = selected ? bookingUrl({
    title: `你好，我想预约${venue.categoryLabel || venue.name}`,
    details: [`档案 #${selected.id}`],
    links: selected.media.map(item =>
      new URL(mediaUrl(`${API_BASE_URL}/delivery-media/${item.id}`), window.location.origin).href),
  }) : ''
  useEffect(() => {
    setData(null)
    setSelected(null)
  }, [venue.id, venue.categoryKey])
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
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && !selected) setAttempt(value => value + 1)
    }, 60000)
    return () => clearInterval(timer)
  }, [selected])
  const people = (data?.people || []).filter(person => !attendanceOnly || person.on_duty)
  const pageMedia = selected ? photoFirst ? selected.media.slice(mediaIndex, mediaIndex + 1) : selected.media : []
  const move = direction => setMediaIndex(index => (index + direction + selected.media.length) % selected.media.length)
  return <section className={`staff-gallery${photoFirst ? ' mingxi-photo-gallery' : ''}`} aria-label={photoFirst ? attendanceOnly ? '外送出勤照片' : '外送人员相册' : '外送人员档案'}>
    <div className="staff-heading">
      <h2>{photoFirst ? <>{attendanceOnly ? data?.shift || venue.current_shift || '晚班' : '相册'}
        {data && <span className="mingxi-personnel-count"> · {people.length}位</span>}</> : '人员档案'}</h2>
      <button type="button" onClick={() => setAttempt(value => value + 1)}>刷新</button>
    </div>
    {error && <p role="alert" className="membership-error">{error}</p>}
    {!data && !error && <p role="status">正在读取人员档案…</p>}
    {data && <>
      {photoFirst ? <p className="staff-meta">{attendanceOnly ? '仅晚班展示出勤，出勤发布不代表实时在岗' : '点击照片查看完整相册'}</p> : <>
      <p className="staff-meta">全部人员 · {data.people.length} 份档案 · {data.shift_date} {data.shift}。
        未见照片不代表实际缺勤。</p>
      <p className="staff-meta">{subscription.authenticated
        ? '可持续订阅未出勤档案，每个晚班新发布出勤时私信提醒一次，直到取消。'
        : '请从 Telegram 主入口打开，才能订阅出勤提醒。'}</p>
      <SubscriptionFeedback subscription={subscription} />
      </>}
      {!people.length && <p className="staff-meta">{attendanceOnly
        ? data.shift === '白班' ? `西港外送仅展示晚班出勤，白班请联系${contactName()}，可切换人员相册查看照片。`
          : '当前没有可展示的晚班出勤档案，可切换人员相册查看照片。'
        : '暂无可展示的人员档案。'}</p>}
      <div className="staff-grid">
        {people.map(person => {
          const cover = person.media.find(item => item.type === 'photo')
          return <article className="staff-card" key={person.id}>
            <button type="button" className="staff-open" onClick={() => {
              setSelected(person)
              setMediaIndex(0)
              setFailedMedia(new Set())
            }}>
              <div className="staff-cover">
                {cover && !failed.has(cover.id)
                  ? <img loading="lazy" alt={`档案 #${person.id}`}
                    src={mediaUrl(`${API_BASE_URL}/delivery-media/${cover.id}?thumb=1`)}
                    onError={() => setFailed(current => new Set([...current, cover.id]))} />
                  : <span>{cover ? '照片暂不可用' : '视频档案 · 点击查看'}</span>}
                <span className={`staff-attendance-overlay${dutyLabel(person) === '今晚出勤' ? ' is-on-duty' : ''}`}>
                  {dutyLabel(person)}
                </span>
              </div>
              <div className="staff-card-info">
                <strong>档案 #{person.id}</strong>
                {photoFirst && <span className="mingxi-staff-price">价格请咨询</span>}
                <small>{person.media.length} 条照片/视频</small>
              </div>
            </button>
            {!photoFirst && <SubscriptionControl id={person.id} onDuty={person.on_duty} subscription={subscription} />}
          </article>
        })}
      </div>
      {selected && <MusicDialog title={`${venue.categoryLabel || venue.name} · 档案 #${selected.id}`} className={`account-sheet staff-sheet${photoFirst ? ' mingxi-photo-sheet' : ''}`}
        scrollBody onClose={() => setSelected(null)}
        footer={<><div className="staff-customer-actions">
          <SubscriptionControl id={selected.id} onDuty={selected.on_duty} subscription={subscription} />
          <SubscriptionFeedback subscription={subscription} />
          {!photoFirst && <p className="staff-meta">{subscription.authenticated
            ? '持续订阅，每个晚班提醒一次，可随时取消。请先在机器人聊天中点击开始。'
            : '请从 Telegram 主入口打开，才能订阅。'}仅提醒订阅后的频道照片新发布，出勤发布不代表实时在岗。</p>}
        </div><CopyBooking key={selected.id} url={contact} openChat={photoFirst} />
        {!photoFirst && <a className="account-primary-link" href={contact}
          target="_blank" rel="noreferrer">联系预约</a>}</>}>
        <p className="staff-meta">{dutyLabel(selected)}</p>
        <div className="staff-detail-media">
        {photoFirst && selected.media.length > 1 && <div className="staff-pages">
          <button type="button" onClick={() => move(-1)}>上一张</button>
          <span>{mediaIndex + 1} / {selected.media.length}</span>
          <button type="button" onClick={() => move(1)}>下一张</button>
        </div>}
        {pageMedia.map(item => <div key={item.id}
          onTouchStart={event => { touchStart.current = event.touches[0].clientX }}
          onTouchEnd={event => {
            if (photoFirst && selected.media.length > 1 && touchStart.current !== null) {
              const distance = event.changedTouches[0].clientX - touchStart.current
              if (Math.abs(distance) >= 60) move(distance > 0 ? -1 : 1)
            }
            touchStart.current = null
          }}
          onTouchCancel={() => { touchStart.current = null }}>
          {failedMedia.has(item.id) ? <p role="alert">媒体加载失败，请关闭后重试。</p> :
            item.type === 'video' ? <video className="staff-detail-photo" controls playsInline preload="metadata"
              src={mediaUrl(`${API_BASE_URL}/delivery-media/${item.id}`)}
              onError={() => setFailedMedia(current => new Set([...current, item.id]))} /> :
              <img className="staff-detail-photo" loading="lazy" alt={`档案 #${selected.id}`}
                src={mediaUrl(`${API_BASE_URL}/delivery-media/${item.id}`)}
                onError={() => setFailedMedia(current => new Set([...current, item.id]))} />}
        </div>)}
        </div>
      </MusicDialog>}
    </>}
  </section>
}
