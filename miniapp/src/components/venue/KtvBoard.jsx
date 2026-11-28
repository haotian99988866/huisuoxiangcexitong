import ModuleIcon from '../ModuleIcon'
import CopyBooking from '../CopyBooking'
import LazyMedia from '../media/LazyMedia'
import ContactButton from '../media/ContactButton'
import { contactName, mediaUrl } from '../../utils/tenant'
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
import './KtvBoard.css'

export default function KtvBoard({
  selectedVenue,
  ktvVideo,
  setKtvVideo,
  closeKtvVideo,
}) {
  return (
    <div className="ktv-board" style={{ ...attendancePanelStyle, marginBottom: '24px' }}>
      <div style={{ ...attendanceHeadingRowStyle, marginBottom: '12px' }}>
        <h3 style={attendanceInlineHeadingStyle}><ModuleIcon name="movies" />KTV包房视频</h3>
        <span style={recentUpdateStyle}>
          最近更新：{formatChannelMessageTime(selectedVenue.last_channel_message_at)}
        </span>
      </div>
      {(selectedVenue.ktv_videos || []).length > 0 ? (
        <div className="ktv-room-grid">
          {selectedVenue.ktv_videos.map(video => {
            const info = ktvVideoInfo(video)
            return (
            <div key={video.id} className="ktv-room-card">
              <div className="ktv-room-cover">
                <LazyMedia
                  as="video"
                  preload="metadata"
                  muted
                  playsInline
                  src={mediaUrl(`${API_BASE_URL}/ktv-videos/${video.id}#t=0.5`)}
                  style={{ width: '100%', height: '100%', objectFit: 'cover', pointerEvents: 'none' }}
                />
                <button type="button" className="ktv-room-play"
                  aria-label={`播放${info.hall}${video.room_number ? ` ${video.room_number}` : ''}视频`}
                  onClick={() => setKtvVideo(video)}>
                  {video.room_number && <span className="ktv-room-number">{video.room_number}</span>}
                  <span className="ktv-room-play-icon" aria-hidden="true">▶</span>
                  {info.type && <span className="ktv-room-type">{info.type}</span>}
                </button>
              </div>
              <div className="ktv-room-details">
                <div className="ktv-room-title">
                  {info.hall}{video.room_number ? ` ${video.room_number}` : ''}
                </div>
                <CopyBooking url={ktvBookingUrl(video)} compact label={`复制并联系${contactName()}`} />
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