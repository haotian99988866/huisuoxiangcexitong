import { useEffect, useMemo, useState } from 'react'
import ModuleIcon from '../ModuleIcon'
import CopyBooking from '../CopyBooking'
import LazyMedia from '../media/LazyMedia'
import NoticeCard from '../media/NoticeCard'
import { contactName } from '../../utils/tenant'
import { mediaUrl, tenantHeaders } from '../../utils/tenant'
import { API_BASE_URL } from '../../utils/twa'
import { massageRosterMembers, massageRosterPhoto, massageRosterServices } from '../../utils/massageRoster'
import {
  attendanceHeadingRowStyle,
  attendanceInlineHeadingStyle,
  attendancePanelStyle,
  CHANNEL_PHOTO_VENUE_IDS,
  formatChannelMessageTime,
  getAtlanticCategoryCodes,
  getDihaoCategoryCodes,
  getKaidiCategoryCodes,
  lazyFillStyle,
  photoViewerButtonStyle,
  recentUpdateStyle,
  technicianGridStyle,
} from '../../utils/venueHome'

export default function TechnicianBoard({
  selectedVenue,
  onDutyCount,
  dutyLabel,
  currentShift,
  dutyShiftToday,
  technicianList,
  rosterGridStyle,
  rosterGroups,
  attendanceServiceGroups,
  serviceGroups,
  selectedTechnicianCode,
  setSelectedTechnicianCode,
  technicianPhotos,
  technicianProfile,
  technicianServiceCategories,
  contactManagerUrl,
  photoViewerIndex,
  setPhotoViewerIndex,
  photoZoom,
  setPhotoZoom,
  photoLoadState,
  setPhotoLoadState,
  photoRetry,
  setPhotoRetry,
  widePhotos,
  setWidePhotos,
}) {
  const [archiveMembers, setArchiveMembers] = useState([])
  const [archiveError, setArchiveError] = useState('')
  const [archiveAttempt, setArchiveAttempt] = useState(0)
  const isMassage = Number(selectedVenue.id) === 1
  useEffect(() => {
    if (!isMassage) return undefined
    const controller = new AbortController()
    let timedOut = false
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, 15000)
    setArchiveError('')
    fetch(`${API_BASE_URL}/webhook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
      body: JSON.stringify({ action: 'get_staff', venue_id: 1, mode: 'all' }),
      cache: 'no-store',
      signal: controller.signal,
    }).then(async response => {
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || '人员档案读取失败')
      if (!Array.isArray(result.members)) throw new Error('人员档案数据格式无效')
      setArchiveMembers(result.members)
    }).catch(error => {
      if (error.name === 'AbortError' && !timedOut) return
      console.error('[Massage roster archives]', error)
      setArchiveError('档案照片暂时无法读取，请重试。')
    }).finally(() => clearTimeout(timeout))
    return () => {
      clearTimeout(timeout)
      controller.abort()
    }
  }, [isMassage, archiveAttempt])
  const membersByCode = useMemo(() => massageRosterMembers(archiveMembers), [archiveMembers])
  const photoFor = code => isMassage
    ? massageRosterPhoto(code, membersByCode)
    : (selectedVenue.technician_photos?.[code]?.length
      ? { id: selectedVenue.technician_photos[code][0], kind: 'photos', count: selectedVenue.technician_photos[code].length }
      : null)
  const openTechnician = code => setSelectedTechnicianCode(code)
  const selectedMassagePhoto = isMassage && selectedTechnicianCode ? photoFor(selectedTechnicianCode) : null
  const selectedMassageServices = isMassage && selectedTechnicianCode
    ? massageRosterServices(selectedTechnicianCode, selectedVenue.service_items || [])
    : []
  const renderTechnicianTile = code => {
    const photo = photoFor(code)
    return (
      <button
        key={code}
        type="button"
        onClick={() => openTechnician(code)}
        aria-label={`查看技师 ${code} 的详情${photo ? `，${photo.count}张照片` : '，暂无照片'}`}
        style={{
          position: 'relative',
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'center',
          minWidth: 0,
          minHeight: '72px',
          aspectRatio: '0.82',
          padding: '8px 4px',
          overflow: 'hidden',
          border: photo
            ? '1px solid rgba(60, 45, 35, 0.12)'
            : '1px solid var(--ink-line)',
          borderRadius: '12px',
          backgroundColor: photo ? '#e9e2d9' : 'var(--ink-surface)',
          backgroundImage: photo
            ? 'none'
            : 'linear-gradient(145deg, #f6eee1, #e8edf5)',
          color: photo ? '#fff' : 'var(--ink-accent)',
          fontSize: '14px',
          fontWeight: 700,
          textShadow: photo ? '0 1px 3px rgba(0, 0, 0, 0.5)' : 'none',
          cursor: 'pointer',
          boxShadow: '0 2px 5px rgba(40, 65, 48, 0.08)'
        }}
      >
        {photo && (
          <>
            <LazyMedia
              alt=""
              decoding="async"
              src={mediaUrl(`${API_BASE_URL}/${photo.kind}/${photo.id}/image?thumb=1`)}
              style={lazyFillStyle}
            />
            <span style={{
              position: 'absolute', inset: 0,
              background: 'linear-gradient(to top, rgba(20, 20, 20, 0.72), rgba(20, 20, 20, 0.04) 58%)'
            }} />
          </>
        )}
        <span style={{ position: 'relative', zIndex: 1 }}>{code}</span>
      </button>
    )
  }

  const renderServiceItem = item => (
    <div key={item.id} className="home-service-card">
      <div className="home-service-card-heading">
        <div className="home-service-card-name">
          {item.name}
        </div>
        <div className="home-service-card-price">
          {item.price != null ? `$${Number(item.price).toFixed(2)}` : item.duration || '面议'}
        </div>
      </div>
      {item.description && (
        <div className="home-service-card-description">
          {item.description}
        </div>
      )}
      {item.price && (
        <div className="home-service-card-duration">
          <ModuleIcon name="clock" /> {item.duration}
        </div>
      )}
    </div>
  )

  const renderCategoryTechnicians = (items, title) => {
    const technicianCodes = [...new Set(
      items.flatMap(item => item.technician_codes
        ? item.technician_codes.split(',').map(code => code.trim()).filter(Boolean)
        : [])
    )]

    return (
      <div style={{ marginBottom: '10px' }}>
        <div style={{
          display: 'flex',
          alignItems: 'baseline',
          marginBottom: technicianCodes.length > 0 ? '6px' : 0
        }}>
          <h3 style={{
            fontSize: '15px',
            color: 'var(--ink-accent)',
            margin: 0,
            fontWeight: 600
          }}>
            {title}
          </h3>
        </div>
        {technicianCodes.length > 0 && (
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '6px',
            margin: '0 16px'
          }}>
            {technicianCodes.map(code => {
            const photo = photoFor(code)
            return (
              <button
                key={code}
                type="button"
                onClick={() => openTechnician(code)}
                aria-label={`查看技师 ${code} 的详情${photo ? `，${photo.count}张照片` : '，暂无照片'}`}
                style={{
                  display: 'flex',
                  flex: '0 0 24px',
                  width: '24px',
                  height: '24px',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: 0,
                  overflow: 'hidden',
                  border: photo
                    ? '1px solid rgba(60, 45, 35, 0.12)'
                    : '1px solid var(--ink-line)',
                  borderRadius: '50%',
                  backgroundColor: photo ? '#e9e2d9' : 'var(--ink-surface)',
                  position: 'relative',
                  backgroundImage: photo
                    ? 'none'
                    : 'linear-gradient(145deg, #f6eee1, #e8edf5)',
                  color: photo ? '#fff' : 'var(--ink-accent)',
                  fontSize: '8px',
                  fontWeight: 700,
                  textShadow: photo ? '0 1px 2px rgba(0, 0, 0, 0.7)' : 'none',
                  cursor: 'pointer',
                  boxShadow: '0 1px 3px rgba(40, 65, 48, 0.08)'
                }}
              >
                {photo && (
                  <>
                    <LazyMedia
                      alt=""
                      decoding="async"
                      src={mediaUrl(`${API_BASE_URL}/${photo.kind}/${photo.id}/image?thumb=1`)}
                      style={lazyFillStyle}
                    />
                    <span style={{
                      position: 'absolute', inset: 0, background: 'rgba(20, 20, 20, 0.25)'
                    }} />
                  </>
                )}
                <span style={{ position: 'relative' }}>{code}</span>
              </button>
            )
            })}
          </div>
        )}
      </div>
    )
  }

  const venueId = String(selectedVenue.id)
  const getCategoryCodes = category => (
    venueId === '5'
      ? getAtlanticCategoryCodes(selectedVenue, category)
      : venueId === '6'
        ? getDihaoCategoryCodes(selectedVenue, category)
        : getKaidiCategoryCodes(selectedVenue, category)
  )

  const categoryList = selectedVenue.price_catalog || []

  return (
    <>
      {isMassage && archiveError && <p role="alert">
        {archiveError} <button type="button" onClick={() => setArchiveAttempt(value => value + 1)}>重新读取</button>
      </p>}
      {CHANNEL_PHOTO_VENUE_IDS.includes(venueId) && onDutyCount > 0 ? (
        <div style={{ ...attendancePanelStyle, marginBottom: '24px' }}>
          <div style={{ ...attendanceHeadingRowStyle, marginBottom: '12px' }}>
            <h3 style={attendanceInlineHeadingStyle}>
              <ModuleIcon name="personal" />{dutyLabel}技师 ({onDutyCount}张照片)
            </h3>
            <span style={recentUpdateStyle}>
              最近更新：{formatChannelMessageTime(selectedVenue.last_channel_message_at)}
            </span>
          </div>
          {categoryList.filter(category => getCategoryCodes(category).length > 0).map(category => (
            <div key={category.name} style={{ marginBottom: '14px' }}>
              <h4 style={{
                margin: '0 0 8px',
                color: 'var(--ink-accent)',
                fontSize: '13px',
                fontWeight: 600
              }}>
                {category.name}
                {(category.price != null) && (
                  <span style={{ marginLeft: '8px', color: '#888', fontWeight: 400 }}>
                    价格 ${category.price} · 90分钟
                  </span>
                )}
              </h4>
              {getCategoryCodes(category).length > 0 ? (
                <div style={technicianGridStyle}>
                  {getCategoryCodes(category).map(renderTechnicianTile)}
                </div>
              ) : (
                <p style={{ margin: 0, color: '#888', fontSize: '13px' }}>
                  暂无技师工号
                </p>
              )}
            </div>
          ))}
        </div>
      ) : technicianList.length > 0 ? (
        <div style={attendancePanelStyle}>
          <div style={{ ...attendanceHeadingRowStyle, marginBottom: '12px' }}>
            <h3 style={attendanceInlineHeadingStyle}>
              <ModuleIcon name="personal" />{dutyLabel}技师 ({technicianList.length}人)
              {!currentShift && dutyShiftToday && (
                <span style={{ fontSize: '11px', color: '#666', marginLeft: '8px' }}>
                  （{dutyShiftToday}）
                </span>
              )}
            </h3>
            <span style={recentUpdateStyle}>
              最近更新：{formatChannelMessageTime(selectedVenue.last_channel_message_at)}
            </span>
          </div>
          {rosterGroups.length > 0 ? (
            rosterGroups.map(group => (
              <div key={group.group_name} style={{ marginBottom: '14px' }}>
                <h4 style={{
                  margin: '0 0 8px',
                  color: 'var(--ink-accent)',
                  fontSize: '13px',
                  fontWeight: 600
                }}>
                  {group.group_name}
                  {venueId === '2' && (() => {
                    const boardNumber = group.group_name.match(/(\d+)\s*号牌/)?.[1]
                    const price = (selectedVenue.price_catalog || []).find(item => (
                      item.board === `${boardNumber}号牌`
                    ))
                    return price && (
                      <span style={{ marginLeft: '8px', color: '#888', fontWeight: 400 }}>
                        会员 ${price.member} / 90分钟 · 半价 ${price.half} / 45分钟
                      </span>
                    )
                  })()}
                </h4>
                <div style={rosterGridStyle}>
                  {group.technician_codes.map(renderTechnicianTile)}
                </div>
              </div>
            ))
          ) : (
            <div style={rosterGridStyle}>
              {technicianList.map(renderTechnicianTile)}
            </div>
          )}
        </div>
      ) : (
        <NoticeCard
          icon="waiting"
          title={`${dutyLabel}尚未发布`}
          contactOnly
          updatedAt={formatChannelMessageTime(selectedVenue.last_channel_message_at)}
        >
          {currentShift === '白班'
            ? '白班出勤名单与照片发布后将在此展示，'
            : '晚班出勤名单与照片发布后将在此展示，'}
          <br />如需了解详情请直接联系{contactName()}。
        </NoticeCard>
      )}

      {attendanceServiceGroups.length > 0 ? (
        <div style={{
          marginBottom: '24px',
          background: 'var(--home-surface)',
          borderRadius: '12px',
          padding: '16px'
        }}>
          {attendanceServiceGroups.map(group => (
            <div key={group.group_name} style={{ marginBottom: '14px' }}>
              <h3 style={{
                margin: '0 0 8px',
                color: 'var(--ink-accent)',
                fontSize: '15px',
                fontWeight: 600
              }}>
                {group.group_name}
              </h3>
              {group.technician_codes.length > 0 ? (
                <div style={technicianGridStyle}>
                  {group.technician_codes.map(renderTechnicianTile)}
                </div>
              ) : (
                <p style={{ margin: 0, color: '#888', fontSize: '13px' }}>暂无关联技师</p>
              )}
            </div>
          ))}
        </div>
      ) : Object.keys(serviceGroups).length > 0 && (
        <div style={{ marginBottom: '24px' }}>
          {[
            { key: 'small', title: '小项' },
            { key: 'medium', title: '中项' },
            { key: 'high', title: '高项' },
            { key: 'tcm', title: '中医按摩' },
            { key: 'pedicure', title: '修脚师' }
          ].map(({ key, title }) => serviceGroups[key] && (
            <div key={key} style={{ marginBottom: '18px' }}>
              {renderCategoryTechnicians(serviceGroups[key].items, title)}
              <div style={{ display: 'grid', gap: '8px' }}>
                {serviceGroups[key].items.map(renderServiceItem)}
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedTechnicianCode && selectedVenue && (
        <div
          role="presentation"
          onClick={() => setSelectedTechnicianCode('')}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1000,
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'center',
            background: 'rgba(0, 0, 0, 0.48)'
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="technician-detail-title"
            onClick={event => event.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: '520px',
              maxHeight: '82vh',
              overflowY: 'auto',
              padding: '20px 20px calc(24px + env(safe-area-inset-bottom))',
              boxSizing: 'border-box',
              borderRadius: '20px 20px 0 0',
              background: '#fff'
            }}
          >
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '16px'
            }}>
              <h2
                id="technician-detail-title"
                style={{ margin: 0, fontSize: '20px', color: 'var(--ink-text)' }}
              >
                技师 {selectedTechnicianCode}
              </h2>
              <button
                type="button"
                onClick={() => setSelectedTechnicianCode('')}
                aria-label="关闭技师详情"
                style={{
                  width: '36px',
                  height: '36px',
                  border: 0,
                  borderRadius: '50%',
                  background: '#f2f2f2',
                  color: '#555',
                  fontSize: '22px',
                  cursor: 'pointer'
                }}
              >
                ×
              </button>
            </div>

            {isMassage ? (
              <>
                {selectedMassagePhoto && (
                  <img
                    src={mediaUrl(`${API_BASE_URL}/staff-photos/${selectedMassagePhoto.id}/image`)}
                    alt={`工号 ${selectedTechnicianCode} 的照片`}
                    style={{ display: 'block', maxWidth: '100%', maxHeight: '240px', margin: '0 auto 18px', borderRadius: '12px', objectFit: 'contain' }}
                  />
                )}
                <h3 style={{ margin: '0 0 8px', fontSize: '15px', color: 'var(--ink-text)' }}>可服务项目</h3>
                <p style={{ margin: '0 0 12px', fontSize: '12px', color: 'var(--ink-muted)' }}>
                  根据出勤日期关联的项目展示，具体服务请联系确认。
                </p>
                {selectedMassageServices.length > 0 ? (
                  <div style={{ display: 'grid', gap: '8px' }}>
                    {selectedMassageServices.map(item => (
                      <div key={item.id} className="home-service-card">
                        <div className="home-service-card-heading">
                          <div className="home-service-card-name">{item.name}</div>
                          <div className="home-service-card-price">
                            {item.price != null && item.price !== '' ? `$${Number(item.price).toFixed(2)}` : '价格待确认'}
                          </div>
                        </div>
                        {item.description && <div className="home-service-card-description">{item.description}</div>}
                        <div className="home-service-card-duration">
                          <ModuleIcon name="clock" /> {item.duration || '时长待确认'}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : <p style={{ color: 'var(--ink-muted)', fontSize: '14px' }}>暂无已关联的服务项目，请联系确认。</p>}
                <CopyBooking url={contactManagerUrl} />
              </>
            ) : (
            <>
            {technicianPhotos.length > 0 ? (
              <div style={{
                display: 'flex',
                gap: '10px',
                overflowX: 'auto',
                marginBottom: '18px'
              }}>
                {technicianPhotos.map((photoId, index) => (
                  <button
                    key={photoId}
                    type="button"
                    onClick={() => {
                      setPhotoViewerIndex(index)
                      setPhotoZoom(1)
                      setPhotoLoadState('loading')
                      setPhotoRetry(0)
                    }}
                    aria-label={`放大查看第 ${index + 1} 张照片`}
                    style={{
                      position: 'relative',
                      padding: 0,
                      border: 0,
                      borderRadius: '12px',
                      background: '#f2f2f2',
                      overflow: 'hidden',
                      cursor: 'zoom-in',
                      flex: '0 0 auto'
                    }}
                  >
                    <img
                      src={mediaUrl(`${API_BASE_URL}/photos/${photoId}/image`)}
                      alt={`工号 ${selectedTechnicianCode} 的第 ${index + 1} 张照片`}
                      loading="lazy"
                      onLoad={event => {
                        const image = event.currentTarget
                        if (image.naturalWidth > image.naturalHeight) {
                          setWidePhotos(current => (
                            current[photoId]
                              ? current
                              : { ...current, [photoId]: true }
                          ))
                        }
                      }}
                      style={{
                        display: 'block',
                        width: widePhotos[photoId]
                          ? 'min(88vw, 420px)'
                          : 'min(48vw, 220px)',
                        height: '280px',
                        objectFit: widePhotos[photoId] ? 'contain' : 'cover'
                      }}
                    />
                    <span style={{
                      position: 'absolute',
                      right: '8px',
                      bottom: '8px',
                      padding: '4px 8px',
                      borderRadius: '12px',
                      background: 'rgba(0, 0, 0, 0.58)',
                      color: '#fff',
                      fontSize: '12px'
                    }}>
                      点击放大
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <div style={{
                marginBottom: '18px',
                padding: '24px 12px',
                borderRadius: '12px',
                background: '#f7f7f7',
                color: '#888',
                textAlign: 'center',
                fontSize: '14px'
              }}>
                暂无已确认照片
              </div>
            )}

            {Object.keys(technicianProfile).length > 0 && (
              <div style={{ marginBottom: '18px' }}>
                <h3 style={{
                  margin: '0 0 9px',
                  paddingLeft: '8px',
                  borderLeft: '3px solid #b59460',
                  fontSize: '15px',
                  lineHeight: 1.2,
                  color: 'var(--ink-text)'
                }}>
                  技师基本信息
                </h3>
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
                  padding: '4px 12px',
                  border: '1px solid var(--ink-line)',
                  borderRadius: '12px',
                  background: 'var(--ink-reading)'
                }}>
                  {[
                    ['technician_code', '工号'],
                    ['age', '年龄'],
                    ['height', '身高'],
                    ['bust', '胸围'],
                    ['weight', '体重'],
                    ['origin', CHANNEL_PHOTO_VENUE_IDS.includes(venueId) ? '产地' : '籍贯']
                  ].filter(([field]) => technicianProfile[field]).map(([field, label]) => (
                    <div
                      key={field}
                      style={{
                        display: 'flex',
                        alignItems: 'baseline',
                        gap: '8px',
                        minWidth: 0,
                        padding: '9px 2px',
                        borderBottom: '1px solid var(--ink-line)'
                      }}
                    >
                      <span style={{
                        flexShrink: 0,
                        color: 'var(--ink-muted)',
                        fontSize: '12px'
                      }}>{label}</span>
                      <strong style={{
                        minWidth: 0,
                        color: 'var(--ink-text)',
                        fontSize: '14px',
                        fontWeight: 650,
                        overflowWrap: 'anywhere'
                      }}>
                        {technicianProfile[field]}
                        {field === 'height' ? ' cm' : field === 'age' ? ' 岁' : ''}
                      </strong>
                    </div>
                  ))}
                </div>
                {technicianProfile.service_description && (
                  <div style={{ marginTop: '14px' }}>
                    <h3 style={{ margin: '0 0 8px', fontSize: '15px', color: '#444' }}>
                      服务介绍
                    </h3>
                    <p style={{
                      margin: 0,
                      padding: '12px',
                      borderRadius: '10px',
                      background: 'var(--ink-surface)',
                      color: '#666',
                      fontSize: '13px',
                      lineHeight: 1.7,
                      whiteSpace: 'pre-wrap',
                      overflowWrap: 'anywhere'
                    }}>
                      {technicianProfile.service_description}
                    </p>
                  </div>
                )}
              </div>
            )}

            <div style={{ marginBottom: '16px' }}>
              <h3 style={{ margin: '0 0 6px', fontSize: '15px', color: '#444' }}>
                信息来源
              </h3>
              <div style={{ fontSize: '14px', color: '#666' }}>
                {CHANNEL_PHOTO_VENUE_IDS.includes(venueId)
                  ? '会所频道照片'
                  : technicianList.includes(selectedTechnicianCode)
                    ? `在岗${dutyShiftToday ? ` · ${dutyShiftToday}` : ''}`
                    : `不在${dutyLabel}名单`}
              </div>
            </div>

            {technicianServiceCategories.length > 0 && (
              <div>
                <h3 style={{ margin: '0 0 8px', fontSize: '15px', color: '#444' }}>
                  可服务类别
                </h3>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {technicianServiceCategories.map(category => (
                    <span
                      key={category}
                      style={{
                        padding: '6px 11px',
                        borderRadius: '16px',
                        background: 'var(--ink-wash)',
                        color: 'var(--ink-accent)',
                        fontSize: '13px'
                      }}
                    >
                      {category}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <CopyBooking url={contactManagerUrl} />
            </>
            )}
          </section>
        </div>
      )}
      {photoViewerIndex !== null && technicianPhotos[photoViewerIndex] && (
        <div
          role="presentation"
          onClick={() => {
            setPhotoViewerIndex(null)
            setPhotoZoom(1)
            setPhotoLoadState('idle')
          }}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1100,
            display: 'flex',
            flexDirection: 'column',
            background: 'rgba(0, 0, 0, 0.96)',
            color: '#fff'
          }}
        >
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: 'max(16px, env(safe-area-inset-top)) 16px 12px'
          }}>
            <span style={{ fontSize: '14px' }}>
              工号 {selectedTechnicianCode} · {photoViewerIndex + 1}/{technicianPhotos.length}
            </span>
            <button
              type="button"
              onClick={() => {
                setPhotoViewerIndex(null)
                setPhotoZoom(1)
                setPhotoLoadState('idle')
              }}
              aria-label="关闭照片查看"
              style={{
                width: '40px',
                height: '40px',
                border: 0,
                borderRadius: '50%',
                background: 'rgba(255, 255, 255, 0.16)',
                color: '#fff',
                fontSize: '24px',
                cursor: 'pointer'
              }}
            >
              ×
            </button>
          </div>

          <div
            onClick={event => event.stopPropagation()}
            style={{
              position: 'relative',
              flex: 1,
              minHeight: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              overflow: 'auto',
              padding: '8px 12px'
            }}
          >
            <img
              key={`${technicianPhotos[photoViewerIndex]}-${photoRetry}`}
              src={mediaUrl(`${API_BASE_URL}/photos/${technicianPhotos[photoViewerIndex]}/image${photoRetry ? `?retry=${photoRetry}` : ''}`)}
              alt={`工号 ${selectedTechnicianCode} 的放大照片`}
              onLoad={() => setPhotoLoadState('loaded')}
              onError={() => setPhotoLoadState('error')}
              style={{
                display: 'block',
                width: `${Math.min(92 * photoZoom, 300)}vw`,
                height: `${Math.min(82 * photoZoom, 120)}vh`,
                maxWidth: 'none',
                maxHeight: 'none',
                objectFit: 'contain',
                opacity: photoLoadState === 'loaded' ? 1 : 0
              }}
            />
            {photoLoadState !== 'loaded' && (
              <div style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '14px',
                color: '#fff',
                textAlign: 'center',
                fontSize: '14px'
              }}>
                <span>
                  {photoLoadState === 'error' ? '照片加载失败，请检查网络后重试' : '正在加载照片…'}
                </span>
                {photoLoadState === 'error' && (
                  <button
                    type="button"
                    onClick={() => {
                      setPhotoLoadState('loading')
                      setPhotoRetry(retry => retry + 1)
                    }}
                    style={{
                      padding: '9px 16px',
                      border: '1px solid rgba(255, 255, 255, 0.6)',
                      borderRadius: '18px',
                      background: 'rgba(255, 255, 255, 0.14)',
                      color: '#fff',
                      fontSize: '14px',
                      cursor: 'pointer'
                    }}
                  >
                    重新加载
                  </button>
                )}
              </div>
            )}
          </div>

          <div
            onClick={event => event.stopPropagation()}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '16px',
              padding: '12px 16px max(20px, env(safe-area-inset-bottom))'
            }}
          >
            {technicianPhotos.length > 1 && (
              <button
                type="button"
                onClick={() => {
                  setPhotoViewerIndex(index => (index - 1 + technicianPhotos.length) % technicianPhotos.length)
                  setPhotoZoom(1)
                  setPhotoLoadState('loading')
                  setPhotoRetry(0)
                }}
                aria-label="上一张照片"
                style={{ ...photoViewerButtonStyle, width: '58px', fontSize: '12px' }}
              >
                上一张
              </button>
            )}
            <button
              type="button"
              onClick={() => setPhotoZoom(zoom => Math.max(1, zoom - 0.5))}
              aria-label="缩小照片"
              style={photoViewerButtonStyle}
            >
              −
            </button>
            <span style={{ minWidth: '48px', textAlign: 'center', fontSize: '14px' }}>
              {Math.round(photoZoom * 100)}%
            </span>
            <button
              type="button"
              onClick={() => setPhotoZoom(zoom => Math.min(3, zoom + 0.5))}
              aria-label="放大照片"
              style={photoViewerButtonStyle}
            >
              +
            </button>
            {technicianPhotos.length > 1 && (
              <button
                type="button"
                onClick={() => {
                  setPhotoViewerIndex(index => (index + 1) % technicianPhotos.length)
                  setPhotoZoom(1)
                  setPhotoLoadState('loading')
                  setPhotoRetry(0)
                }}
                aria-label="下一张照片"
                style={{ ...photoViewerButtonStyle, width: '58px', fontSize: '12px' }}
              >
                下一张
              </button>
            )}
          </div>
        </div>
      )}
    </>
  )
}