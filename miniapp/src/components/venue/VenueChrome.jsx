import { useEffect, useRef, useState } from 'react'
import { AnnouncementCard, FullscreenAnnouncementBar } from '../Announcement'
import ModuleTabs from '../ModuleTabs'
import { tenantPath } from '../../utils/tenant'
import { deliveryCategoryKey, deliveryCategoryLabel } from '../../utils/venueHome'

function moveMenuFocus(event, setOpen, menuSelector) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  setOpen(true)
  requestAnimationFrame(() => {
    const options = [...document.querySelectorAll(`${menuSelector} [role="option"]`)]
    if (!options.length) return
    const current = options.indexOf(document.activeElement)
    const next = event.key === 'Home' ? 0
      : event.key === 'End' ? options.length - 1
        : current < 0 ? event.key === 'ArrowUp' ? options.length - 1 : 0
          : (current + (event.key === 'ArrowUp' ? -1 : 1) + options.length) % options.length
    options[next]?.focus()
  })
}

export default function VenueChrome({
  isFullscreen,
  loading,
  error,
  selectedId,
  setSelectedId,
  navigate,
  deliveryCategories,
  setDeliveryCategoryId,
  selectedVenue,
  isPhotoFirst,
  showStaffArchive,
  setShowStaffArchive,
  setSelectedTechnicianCode,
  hasStaffArchive,
  attendanceViewLabel,
  isDeliveryDayShift,
  dutyLabel,
  onDutyCount,
}) {
  const [servicePicker, setServicePicker] = useState(false)
  const servicePickerRef = useRef(null)
  const selectedVenueId = String(selectedVenue?.id || selectedId)
  const mingxiGroup = ['1', '2', '3'].includes(selectedVenueId)
  const selectMingxi = id => {
    setSelectedId(String(id))
    setSelectedTechnicianCode('')
    navigate(tenantPath(`/?module=venue&venue=${id}`), { replace: true })
  }
  useEffect(() => {
    if (!servicePicker) return undefined
    const close = event => {
      if (!servicePickerRef.current?.contains(event.target)) setServicePicker(false)
    }
    const onKey = event => {
      if (event.key === 'Escape') setServicePicker(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', onKey)
    }
  }, [servicePicker])
  return <>
    <div style={{
      position: 'sticky',
      top: 0,
      zIndex: 20,
      padding: '10px 12px 10px',
      margin: '-12px -12px 14px',
      background: 'var(--ink-header)',
      borderBottom: '1px solid var(--ink-line)',
      boxShadow: '0 4px 18px rgba(34, 40, 52, 0.04)'
    }}>
      {isFullscreen && <FullscreenAnnouncementBar />}
      <ModuleTabs active={deliveryCategories.length ? 'delivery' : 'venue'} />
      <div style={{
        display: 'grid',
        gap: '7px'
      }}>
        <div style={{
          padding: '0 2px'
        }}>
          <div className={`venue-personnel-toolbar${mingxiGroup ? ' mingxi-personnel-toolbar' : ' venue-other-toolbar'}`} style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            flexWrap: 'nowrap'
          }}>
            {mingxiGroup && <div className="mingxi-destination-dropdown" ref={servicePickerRef}>
              <button
                type="button"
                className="mingxi-destination-picker"
                aria-haspopup="listbox"
                aria-expanded={servicePicker}
                onClick={() => setServicePicker(value => !value)}
                onKeyDown={event => moveMenuFocus(event, setServicePicker, '.mingxi-service-menu')}
              >
                {selectedVenueId === '1' ? '按摩' : selectedVenueId === '3' ? 'KTV' : '选妃'} <span aria-hidden="true">⌄</span>
              </button>
              {servicePicker && <div className="venue-picker-menu mingxi-service-menu" role="listbox" aria-label="名玺服务"
                onKeyDown={event => moveMenuFocus(event, setServicePicker, '.mingxi-service-menu')}>
                {[
                  ['1', '按摩', '正规按摩'],
                  ['2', '选妃', '出勤与档案'],
                  ['3', 'KTV', '包房与视频'],
                ].map(([id, label, description]) => (
                  <button key={id} type="button" role="option" aria-selected={selectedVenueId === id}
                    onClick={() => { selectMingxi(id); setServicePicker(false) }}>
                    <span><strong>{label}</strong><small>{description}</small></span>
                    <span aria-hidden="true">{selectedVenueId === id ? '✓' : ''}</span>
                  </button>
                ))}
              </div>}
            </div>}
            <div id="venue-board-filter-slot" className={`venue-board-filter-slot${isPhotoFirst ? '' : ' is-empty'}`} />
            {!mingxiGroup && <div className="venue-destination-title">
              <strong>{loading ? '正在读取…' : error || selectedVenue?.name || '会所服务'}</strong>
            </div>}
            {hasStaffArchive && <nav className="mingxi-personnel-tabs" aria-label="人员浏览">
              {[[false, attendanceViewLabel], [true, '技师档案']].map(([archive, label]) =>
                <button key={label} type="button" aria-pressed={showStaffArchive === archive}
                  aria-controls="venue-personnel-content"
                  onClick={() => { setShowStaffArchive(archive); setSelectedTechnicianCode('') }}>{label}</button>)}
            </nav>}
            <div id="venue-subscriptions-slot" className={`venue-subscriptions-slot${showStaffArchive ? '' : ' is-hidden'}`} />
            {!hasStaffArchive && <div className="venue-compact-status" aria-label="当前服务状态">
            {!hasStaffArchive && <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              flex: '0 0 auto',
              minWidth: '104px',
              height: '44px',
              padding: '0 10px',
              background: 'var(--ink-wash)',
              color: 'var(--ink-accent)',
              border: '1px solid #e3d4bb',
              borderRadius: '12px',
              fontSize: '11px',
              fontWeight: 600,
              whiteSpace: 'nowrap'
            }}>
              <span aria-hidden="true">✓</span>
              {String(selectedVenue?.id) === '3' ? (
                <>
                  包房 <strong style={{ fontSize: '15px' }}>{(selectedVenue.ktv_videos || []).length}</strong> 间
                </>
              ) : isDeliveryDayShift ? (
                <>白班请单独联系</>
              ) : (
                <>
                  {dutyLabel} <strong style={{ fontSize: '15px' }}>{onDutyCount}</strong>{' '}
                  {['4', '5', '6'].includes(String(selectedVenue?.id)) ? '张照片' : '人'}
                </>
              )}
            </div>}
            </div>}
          </div>
        </div>
        {deliveryCategories.length > 0 && !isDeliveryDayShift && (
          <div style={{
            display: 'flex',
            gap: '8px',
            overflowX: 'auto',
            marginTop: '8px',
            scrollbarWidth: 'none',
          }}>
            {deliveryCategories.map(venue => {
              const active = deliveryCategoryKey(venue) === deliveryCategoryKey(selectedVenue)
              return (
                <button
                  key={deliveryCategoryKey(venue)}
                  type="button"
                  onClick={() => setDeliveryCategoryId(deliveryCategoryKey(venue))}
                  style={{
                    flex: '1 1 0', minWidth: 0, whiteSpace: 'nowrap', padding: '7px 2px', borderRadius: '18px', cursor: 'pointer',
                    minHeight: '44px',
                    border: '1px solid var(--ink-line)',
                    background: active ? 'var(--ink-accent)' : 'var(--home-surface)',
                    color: active ? '#fffef5' : 'var(--ink-accent)',
                    fontSize: '14px', fontWeight: 600
                  }}
                >
                  {deliveryCategoryLabel(venue)}
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
    {!isFullscreen && <AnnouncementCard />}
  </>
}