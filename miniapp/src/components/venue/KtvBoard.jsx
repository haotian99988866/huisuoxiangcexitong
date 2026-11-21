import ModuleIcon from '../ModuleIcon'
import CopyBooking from '../CopyBooking'
import LazyMedia from '../media/LazyMedia'
import ContactButton from '../media/ContactButton'
import { mediaUrl } from '../../utils/tenant'
import { API_BASE_URL, hasTelegramBackButton } from '../../utils/twa'
import {
  attendanceHeadingRowStyle,
  attendanceInlineHeadingStyle,
  attendancePanelStyle,
  formatChannelMessageTime,
  ktvBookingButtonStyle,
  ktvBookingUrl,
  ktvPriceLines,
  ktvVideoInfo,
  recentUpdateStyle,
} from '../../utils/venueHome'

export default function KtvBoard({
  selectedVenue,
  ktvVideo,
  setKtvVideo,
  closeKtvVideo,
}) {
  return (
    <div style={{ ...attendancePanelStyle, marginBottom: '24px' }}>
      <div style={{ ...attendanceHeadingRowStyle, marginBottom: '12px' }}>
        <h3 style={attendanceInlineHeadingStyle}><ModuleIcon name="movies" />KTV包房视频</h3>
        <span style={recentUpdateStyle}>
          最近更新：{formatChannelMessageTime(selectedVenue.last_channel_message_at)}
        </span>
      </div>
      {(selectedVenue.ktv_videos || []).length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
          {selectedVenue.ktv_videos.map(video => {
            const info = ktvVideoInfo(video)
            return (
            <div key={video.id} style={{
              background: 'var(--home-surface)', borderRadius: '14px', overflow: 'hidden',
              border: '1px solid var(--ink-line)', boxShadow: 'var(--shadow)'
            }}>
              <div
                onClick={() => setKtvVideo(video)}
                style={{
                  position: 'relative', aspectRatio: '16 / 10',
                  background: '#2b2b2b', cursor: 'pointer', overflow: 'hidden'
                }}
              >
                <LazyMedia
                  as="video"
                  preload="metadata"
                  muted
                  playsInline
                  src={mediaUrl(`${API_BASE_URL}/ktv-videos/${video.id}#t=0.5`)}
                  style={{ width: '100%', height: '100%', objectFit: 'cover', pointerEvents: 'none' }}
                />
                <div style={{
                  position: 'absolute', inset: 0,
                  background: 'linear-gradient(to top, rgba(0,0,0,0.55), rgba(0,0,0,0) 55%)'
                }} />
                {video.room_number && (
                  <span style={{
                    position: 'absolute', top: '8px', left: '8px', padding: '2px 9px',
                    borderRadius: '10px', background: 'rgba(128, 100, 60, 0.92)',
                    color: '#fff', fontSize: '12px', fontWeight: 600
                  }}>
                    {video.room_number}
                  </span>
                )}
                <span style={{
                  position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)',
                  width: '44px', height: '44px', borderRadius: '50%',
                  background: 'var(--ink-reading)', color: 'var(--ink-accent)', fontSize: '16px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  paddingLeft: '3px', boxShadow: '0 2px 8px rgba(0,0,0,0.3)'
                }}>▶</span>
                {info.type && (
                  <span style={{
                    position: 'absolute', left: '8px', bottom: '8px',
                    color: '#fff', fontSize: '12px', textShadow: '0 1px 3px rgba(0,0,0,0.6)'
                  }}>
                    {info.type}
                  </span>
                )}
              </div>
              <div style={{
                display: 'grid',
                gap: '6px', padding: '10px'
              }}>
                <div style={{
                  fontSize: '13px', fontWeight: 600, color: 'var(--ink-text)', minWidth: 0,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                }}>
                  {info.hall}{video.room_number ? ` ${video.room_number}` : ''}
                </div>
                <CopyBooking url={ktvBookingUrl(video)} compact />
              </div>
            </div>
            )
          })}
        </div>
      ) : (
        <p style={{ margin: 0, color: '#888', fontSize: '13px' }}>暂无包房视频</p>
      )}
      {ktvVideo && (
        <div
          ref={node => {
            if (node && !document.fullscreenElement) {
              node.requestFullscreen?.().catch(() => {})
            }
          }}
          onClick={() => closeKtvVideo()}
          style={{
            position: 'fixed', inset: 0, zIndex: 1000, background: '#000',
            display: 'flex', flexDirection: 'column', justifyContent: 'center'
          }}
        >
          {!hasTelegramBackButton && (
          <button
            type="button"
            aria-label="关闭"
            onClick={event => { event.stopPropagation(); closeKtvVideo() }}
            style={{
              position: 'absolute',
              top: 'calc(var(--tg-safe-area-inset-top, 0px) + var(--tg-content-safe-area-inset-top, 0px) + 14px)',
              right: '14px', zIndex: 2,
              width: '40px', height: '40px', borderRadius: '50%', border: 0,
              background: 'rgba(0,0,0,0.6)', color: '#fff', fontSize: '22px', cursor: 'pointer'
            }}
          >
            ✕
          </button>
          )}
          <video
            autoPlay
            controls
            controlsList="nofullscreen"
            playsInline
            src={mediaUrl(`${API_BASE_URL}/ktv-videos/${ktvVideo.id}`)}
            onClick={event => event.stopPropagation()}
            style={{ width: '100%', maxHeight: '80vh', background: '#000' }}
          />
          <div
            onClick={event => event.stopPropagation()}
            style={{ display: 'flex', gap: '12px', justifyContent: 'center', padding: '14px' }}
          >
            <CopyBooking key={ktvVideo.id} url={ktvBookingUrl(ktvVideo)} compact />
            <button
              type="button"
              onClick={() => closeKtvVideo()}
              style={{ ...ktvBookingButtonStyle, background: '#555', border: 0 }}
            >
              关闭
            </button>
          </div>
        </div>
      )}
      {(selectedVenue.ktv_texts || []).length > 0 && (
        <div style={{ marginTop: '20px' }}>
          <h3 style={{ ...attendanceInlineHeadingStyle, marginBottom: '10px' }}><ModuleIcon name="recharge" />消费模式及价格</h3>
          <div style={{
            background: 'var(--home-reading-surface)', border: '1px solid var(--ink-line)',
            borderRadius: '14px', padding: '6px 14px'
          }}>
            {selectedVenue.ktv_texts.flatMap(ktvPriceLines).map((line, index) => (
              <p key={index} style={{
                margin: 0, padding: '8px 0', fontSize: '13px', lineHeight: 1.6,
                color: line.includes('消费明细') ? 'var(--ink-accent)' : '#555',
                fontWeight: line.includes('消费明细') ? 700 : 400,
                borderBottom: '1px dashed var(--ink-line)'
              }}>
                {line}
              </p>
            ))}
          </div>
        </div>
      )}
      <div style={{ marginTop: '20px', padding: '20px', textAlign: 'center', background: 'var(--home-surface)', borderRadius: '14px' }}>
        <p style={{ color: 'var(--ink-accent)', marginBottom: '12px' }}>想了解更多包房、消费价格或预订详情？</p>
        <ContactButton title="名玺水汇KTV包房、消费价格及预订详情" />
      </div>
    </div>
  )
}