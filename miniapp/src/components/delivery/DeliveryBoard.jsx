import { useEffect, useRef } from 'react'
import ModuleIcon from '../ModuleIcon'
import CopyBooking from '../CopyBooking'
import LazyMedia from '../media/LazyMedia'
import NoticeCard from '../media/NoticeCard'
import { contactName, mediaUrl } from '../../utils/tenant'
import { API_BASE_URL, hasTelegramBackButton } from '../../utils/twa'
import {
  attendanceHeadingRowStyle,
  attendanceInlineHeadingStyle,
  formatChannelMessageTime,
  recentUpdateStyle,
} from '../../utils/venueHome'

export default function DeliveryBoard({
  selectedVenue,
  isDeliveryDayShift,
  deliveryViewer,
  setDeliveryViewer,
  deliveryIndex,
  setDeliveryIndex,
  openDeliveryViewer,
  deliveryBookingUrl,
}) {
  const deliveryTouchX = useRef(0)

  const stepDeliveryViewer = step => setDeliveryIndex(index => {
    const total = deliveryViewer?.media.length || 1
    return (index + step + total) % total
  })

  useEffect(() => {
    if (!deliveryViewer) return undefined
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [deliveryViewer])

  if (isDeliveryDayShift) {
    return (
      <NoticeCard icon="daylight" title="西港外送只展示晚班出勤" contactOnly>
        当前为白班时间（06:00–18:00），白班外送请单独联系{contactName()}，<br />
        晚班（18:00 起）内容将在此展示。
      </NoticeCard>
    )
  }

  if (!Array.isArray(selectedVenue.delivery_posts)) return null

  return (
    <div style={{ marginBottom: '24px' }}>
      <div style={{ ...attendanceHeadingRowStyle, marginBottom: '12px' }}>
        <h3 style={attendanceInlineHeadingStyle}><ModuleIcon name="photo" />最新上新</h3>
        <span style={recentUpdateStyle}>
          最近更新：{formatChannelMessageTime(selectedVenue.last_channel_message_at)}
        </span>
      </div>
      {selectedVenue.delivery_posts.length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '10px' }}>
          {selectedVenue.delivery_posts.map(post => {
            const description = (post.description || '').trim()
            const cover = post.media.find(media => media.type !== 'video') || post.media[0]
            return (
              <div
                key={post.id}
                onClick={() => openDeliveryViewer(post)}
                style={{
                  background: 'var(--home-surface)',
                  borderRadius: '14px',
                  overflow: 'hidden',
                  boxShadow: '0 2px 10px rgba(80, 60, 30, 0.1)',
                  cursor: 'pointer',
                }}
              >
                <div style={{ position: 'relative', aspectRatio: '3 / 4', background: cover.type === 'video' ? '#1d1a16' : '#f3ede4' }}>
                  {cover.type === 'video' && (
                    <span style={{
                      position: 'absolute', inset: 0, display: 'flex',
                      alignItems: 'center', justifyContent: 'center',
                      color: 'rgba(255,255,255,0.8)', fontSize: '44px', pointerEvents: 'none',
                    }}>▶</span>
                  )}
                  {cover.type === 'video' ? (
                    <LazyMedia
                      as="video"
                      muted
                      playsInline
                      preload="metadata"
                      src={mediaUrl(`${API_BASE_URL}/delivery-media/${cover.id}#t=0.5`)}
                      style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                    />
                  ) : (
                    <LazyMedia
                      alt=""
                      decoding="async"
                      src={mediaUrl(`${API_BASE_URL}/delivery-media/${cover.id}?thumb=1`)}
                      style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                    />
                  )}
                  {post.media.length > 1 && (
                    <span style={{
                      position: 'absolute', right: '8px', bottom: '8px',
                      background: 'rgba(0,0,0,0.55)', color: '#fff',
                      fontSize: '12px', padding: '2px 8px', borderRadius: '10px',
                    }}>
                      <ModuleIcon name="photo" /> {post.media.length}
                    </span>
                  )}
                  {post.price && (
                    <span style={{
                      position: 'absolute', left: '8px', bottom: '8px',
                      background: 'var(--ink-accent)', color: '#fffef5',
                      fontSize: '13px', fontWeight: 700, padding: '2px 9px', borderRadius: '10px',
                    }}>
                      {post.price}$
                    </span>
                  )}
                  {post.media.some(media => media.type === 'video') && (
                    <span style={{
                      position: 'absolute', left: '8px', top: '8px',
                      background: 'rgba(0,0,0,0.55)', color: '#fff',
                      fontSize: '12px', padding: '2px 8px', borderRadius: '10px',
                    }}>
                      ▶ 视频
                    </span>
                  )}
                </div>
                {description && (
                  <div style={{
                    padding: '8px 10px',
                    fontSize: '13px',
                    lineHeight: 1.5,
                    color: 'var(--ink-text)',
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                    wordBreak: 'break-word',
                  }}>
                    {description}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <NoticeCard icon="waiting" title="暂无内容">
          新内容发布后将在此展示，<br />如需了解详情请直接联系{contactName()}。
        </NoticeCard>
      )}
      {deliveryViewer && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1000,
            background: '#000',
            display: 'flex',
            flexDirection: 'column',
            paddingTop: 'calc(var(--tg-safe-area-inset-top, 0px) + var(--tg-content-safe-area-inset-top, 0px))',
            boxSizing: 'border-box',
          }}
        >
          <div
            onTouchStart={event => { deliveryTouchX.current = event.touches[0].clientX }}
            onTouchEnd={event => {
              const delta = event.changedTouches[0].clientX - deliveryTouchX.current
              if (Math.abs(delta) > 50) stepDeliveryViewer(delta < 0 ? 1 : -1)
            }}
            style={{
              flex: 1,
              minHeight: 0,
              position: 'relative',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              overflow: 'hidden',
              touchAction: 'pan-y',
            }}
          >
            {(() => {
              const media = deliveryViewer.media[deliveryIndex]
              return media.type === 'video' ? (
                <video
                  key={media.id}
                  controls
                  playsInline
                  preload="metadata"
                  src={mediaUrl(`${API_BASE_URL}/delivery-media/${media.id}`)}
                  style={{ maxWidth: '100%', maxHeight: '100%' }}
                />
              ) : (
                <img
                  key={media.id}
                  alt=""
                  src={mediaUrl(`${API_BASE_URL}/delivery-media/${media.id}`)}
                  style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
                />
              )
            })()}
            {deliveryViewer.media.length > 1 && ['上一张', '下一张'].map((label, index) => (
              <button
                key={label}
                type="button"
                aria-label={index === 0 ? '上一张' : '下一张'}
                onClick={() => stepDeliveryViewer(index === 0 ? -1 : 1)}
                style={{
                  position: 'absolute',
                  top: '50%',
                  [index === 0 ? 'left' : 'right']: '8px',
                  transform: 'translateY(-50%)',
                  minWidth: '58px',
                  height: '38px',
                  borderRadius: '19px',
                  border: 'none',
                  background: 'rgba(0,0,0,0.4)',
                  color: '#fff',
                  fontSize: '12px',
                  lineHeight: '34px',
                  padding: '0 8px',
                }}
              >
                {label}
              </button>
            ))}
            {deliveryViewer.media.length > 1 && (
              <span style={{
                position: 'absolute',
                top: 'max(18px, env(safe-area-inset-top))',
                left: '14px',
                background: 'rgba(0,0,0,0.5)',
                color: '#fff',
                fontSize: '13px',
                padding: '3px 10px',
                borderRadius: '12px',
              }}>
                {deliveryIndex + 1} / {deliveryViewer.media.length}
              </span>
            )}
          </div>
          {deliveryViewer.media.length > 1 && (
            <div style={{
              display: 'flex',
              gap: '8px',
              justifyContent: 'center',
              padding: '8px 12px',
              background: '#000',
            }}>
              {deliveryViewer.media.map((media, index) => {
                const thumbStyle = {
                  width: '52px',
                  height: '52px',
                  objectFit: 'cover',
                  borderRadius: '8px',
                  opacity: index === deliveryIndex ? 1 : 0.5,
                  border: index === deliveryIndex ? '2px solid #e0c18f' : '2px solid transparent',
                  boxSizing: 'border-box',
                  background: '#222',
                }
                return media.type === 'video' ? (
                  <div
                    key={media.id}
                    onClick={() => setDeliveryIndex(index)}
                    style={{ position: 'relative', width: '52px', height: '52px' }}
                  >
                    <LazyMedia
                      as="video"
                      muted
                      playsInline
                      preload="metadata"
                      src={mediaUrl(`${API_BASE_URL}/delivery-media/${media.id}#t=0.5`)}
                      style={{ ...thumbStyle, pointerEvents: 'none' }}
                    />
                    <span style={{
                      position: 'absolute', inset: 0, display: 'flex',
                      alignItems: 'center', justifyContent: 'center',
                      color: '#fff', fontSize: '16px', pointerEvents: 'none',
                    }}>▶</span>
                  </div>
                ) : (
                  <LazyMedia
                    key={media.id}
                    alt=""
                    decoding="async"
                    onClick={() => setDeliveryIndex(index)}
                    src={mediaUrl(`${API_BASE_URL}/delivery-media/${media.id}?thumb=1`)}
                    style={thumbStyle}
                  />
                )
              })}
            </div>
          )}
          <div style={{
            padding: '12px 16px max(14px, env(safe-area-inset-bottom))',
            background: '#1b1610',
            color: '#fff',
          }}>
            {(deliveryViewer.work_no || deliveryViewer.price) && (
              <div style={{
                display: 'flex', gap: '12px', alignItems: 'center',
                fontSize: '15px', fontWeight: 700, marginBottom: '8px',
              }}>
                {deliveryViewer.work_no && <span>工号 {deliveryViewer.work_no}</span>}
                {deliveryViewer.price && (
                  <span style={{ color: '#e8c46a' }}>{deliveryViewer.price}$</span>
                )}
              </div>
            )}
            {deliveryViewer.description && (
              <div style={{
                fontSize: '15px',
                lineHeight: 1.6,
                maxHeight: '22vh',
                overflowY: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                marginBottom: '10px',
              }}>
                {deliveryViewer.description}
              </div>
            )}
            <CopyBooking url={deliveryBookingUrl} />
          </div>
          {!hasTelegramBackButton && (
          <button
            type="button"
            aria-label="关闭"
            onClick={() => setDeliveryViewer(null)}
            style={{
              position: 'absolute',
              top: 'max(12px, env(safe-area-inset-top))',
              right: '12px',
              width: '40px',
              height: '40px',
              borderRadius: '50%',
              border: 'none',
              background: 'rgba(255,255,255,0.25)',
              color: '#fff',
              fontSize: '22px',
            }}
          >
            ✕
          </button>
          )}
        </div>
      )}
    </div>
  )
}