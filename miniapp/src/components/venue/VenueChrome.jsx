import { AnnouncementCard, FullscreenAnnouncementBar } from '../Announcement'
import ModuleTabs from '../ModuleTabs'
import { tenantPath } from '../../utils/tenant'
import { sendData } from '../../utils/twa'
import { deliveryCategoryKey, deliveryCategoryLabel } from '../../utils/venueHome'

export default function VenueChrome({
  isFullscreen,
  loading,
  error,
  list,
  selectedId,
  setSelectedId,
  navigate,
  deliveryCategories,
  deliveryCategoryId,
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
  return (
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
          <div className={isPhotoFirst ? 'mingxi-personnel-toolbar' : undefined} style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            flexWrap: 'nowrap'
          }}>
            <select
              value={selectedId}
              onChange={e => {
                const id = e.target.value
                setSelectedId(id)
                sendData({ action: 'get_venues' })
                const venue = list.find(item => String(item.id) === id)
                navigate(tenantPath(venue?.delivery_categories ? '/?module=delivery' : '/?module=venue'), { replace: true })
              }}
              aria-label="选择会所"
              style={{
                flex: '1 1 0',
                minWidth: '0',
                height: '44px',
                padding: '0 30px 0 12px',
                border: '1px solid var(--ink-line)',
                borderRadius: '12px',
                color: 'var(--ink-accent)',
                fontSize: '14px',
                fontWeight: 600,
                outline: 'none',
                appearance: 'none',
                WebkitAppearance: 'none',
                background: "var(--ink-reading) url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' fill='none' stroke='%23746b5d' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E\") no-repeat right 12px center",
                boxShadow: '0 1px 3px rgba(120, 90, 50, 0.06)'
              }}
            >
              {loading ? (
                <option value="">正在读取…</option>
              ) : error ? (
                <option value="">{error}</option>
              ) : (
                list.map(v => (
                  <option key={v.id} value={String(v.id)}>
                    {v.name}
                  </option>
                ))
              )}
            </select>
            {isPhotoFirst && <nav className="mingxi-personnel-tabs" aria-label="人员浏览">
              {[[false, attendanceViewLabel], [true, '人员相册']].map(([archive, label]) =>
                <button key={label} type="button" aria-pressed={showStaffArchive === archive}
                  aria-controls="venue-personnel-content"
                  onClick={() => { setShowStaffArchive(archive); setSelectedTechnicianCode('') }}>{label}</button>)}
            </nav>}
            {!isPhotoFirst && <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', flex: '0 0 auto' }}>
              {hasStaffArchive && <button
                type="button"
                onClick={() => {
                  setShowStaffArchive(value => !value)
                  setSelectedTechnicianCode('')
                }}
                aria-controls="venue-personnel-content"
                style={{
                  minHeight: '44px', padding: '0 10px', borderRadius: '12px',
                  border: '1px solid var(--ink-line)', background: 'var(--ink-reading)',
                  color: 'var(--ink-accent)', fontSize: '12px', fontWeight: 600, cursor: 'pointer'
                }}
              >{showStaffArchive ? attendanceViewLabel : '人员档案'}</button>}
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
        {!isFullscreen && <AnnouncementCard />}
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
  )
}