import { useEffect, useState, useMemo } from 'react'
import { API_BASE_URL, sendData } from '../utils/twa'

const photoViewerButtonStyle = {
  width: '44px',
  height: '44px',
  border: '1px solid rgba(255, 255, 255, 0.35)',
  borderRadius: '50%',
  background: 'rgba(255, 255, 255, 0.12)',
  color: '#fff',
  fontSize: '26px',
  cursor: 'pointer'
}

export default function VenueList() {
  const [list, setList] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [selectedTechnicianCode, setSelectedTechnicianCode] = useState('')
  const [photoViewerIndex, setPhotoViewerIndex] = useState(null)
  const [photoZoom, setPhotoZoom] = useState(1)
  const [photoLoadState, setPhotoLoadState] = useState('idle')
  const [photoRetry, setPhotoRetry] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setSelectedTechnicianCode('')
    setPhotoViewerIndex(null)
    setPhotoLoadState('idle')
    setPhotoRetry(0)
  }, [selectedId])

  useEffect(() => {
    const fn = (e) => {
      if (e.data?.type !== 'bot_response') return
      if (e.data.payload?.error) {
        setError(e.data.payload.error)
        setLoading(false)
        return
      }
      const data = e.data.payload?.list || e.data.payload?.venues || e.data.payload
      if (Array.isArray(data)) {
        setList(data)
        setSelectedId(currentId => currentId || (data[0] ? String(data[0].id) : ''))
      }
      setLoading(false)
    }
    window.addEventListener('message', fn)
    sendData({ action: 'get_venues' })
    return () => window.removeEventListener('message', fn)
  }, [])

  const selectedVenue = useMemo(() => {
    const target = list.find(v => String(v.id) === selectedId)
    if (!target && list.length > 0) {
      setSelectedId(String(list[0].id))
      return list[0]
    }
    return target
  }, [list, selectedId])

  const onDutyCount = selectedVenue?.on_duty_count ?? 0
  const technicianList = selectedVenue?.technician_list || []
  const dutyShiftToday = selectedVenue?.duty_shift_today
  const serviceItems = selectedVenue?.service_items || []
  const technicianPhotos = selectedVenue?.technician_photos?.[selectedTechnicianCode] || []

  useEffect(() => {
    if (!selectedTechnicianCode) return undefined
    const closeOnEscape = event => {
      if (event.key === 'Escape') {
        if (photoViewerIndex !== null) {
          setPhotoViewerIndex(null)
          setPhotoZoom(1)
          setPhotoLoadState('idle')
        } else {
          setSelectedTechnicianCode('')
        }
      }
      if (photoViewerIndex === null || technicianPhotos.length === 0) return
      if (event.key === 'ArrowLeft') {
        setPhotoViewerIndex(index => (index - 1 + technicianPhotos.length) % technicianPhotos.length)
        setPhotoZoom(1)
        setPhotoLoadState('loading')
      }
      if (event.key === 'ArrowRight') {
        setPhotoViewerIndex(index => (index + 1) % technicianPhotos.length)
        setPhotoZoom(1)
        setPhotoLoadState('loading')
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [photoViewerIndex, selectedTechnicianCode, technicianPhotos])

  // 按项目分类分组
  const serviceGroups = useMemo(() => {
    const groups = {}
    serviceItems.forEach(item => {
      if (!groups[item.category]) {
        groups[item.category] = { name: item.category_name, items: [] }
      }
      groups[item.category].items.push(item)
    })
    return groups
  }, [serviceItems])

  const technicianServiceCategories = selectedTechnicianCode
    ? [
        { key: 'small', title: '小项' },
        { key: 'medium', title: '中项' },
        { key: 'high', title: '高项' },
        { key: 'tcm', title: '中医按摩' },
        { key: 'pedicure', title: '修脚' }
      ]
        .filter(({ key }) => serviceGroups[key]?.items.some(item => (
          item.technician_codes?.split(',').map(code => code.trim())
            .includes(selectedTechnicianCode)
        )))
        .map(({ title }) => title)
    : []

  const contactManagerUrl = selectedTechnicianCode && selectedVenue
    ? (() => {
        const photoLinks = selectedVenue.technician_photo_links?.[selectedTechnicianCode] || []
        const message = [
          '你好，我想咨询这位技师：',
          `会所：${selectedVenue.name}`,
          `工号：${selectedTechnicianCode}`,
          `出勤：${dutyShiftToday || '今日在岗'}`,
          `可服务类别：${technicianServiceCategories.join('、') || '暂无类别信息'}`,
          ...(photoLinks.length > 0
            ? ['', '频道照片原帖：', ...photoLinks.map((link, index) => `${index + 1}. ${link}`)]
            : ['技师暂无照片'])
        ].join('\n')
        return `https://t.me/haotian99988866?text=${encodeURIComponent(message)}`
      })()
    : 'https://t.me/haotian99988866'

  const renderServiceItem = item => (
    <div key={item.id} style={{
      background: '#fff',
      borderRadius: '10px',
      padding: '14px 16px'
    }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
        gap: '8px'
      }}>
        <div style={{ fontSize: '14px', fontWeight: 600, color: '#333' }}>
          {item.name}
        </div>
        <div style={{
          fontSize: '15px',
          fontWeight: 700,
          color: '#c53030',
          flexShrink: 0
        }}>
          {item.price != null ? `$${Number(item.price).toFixed(2)}` : item.duration || '面议'}
        </div>
      </div>
      {item.description && (
        <div style={{ fontSize: '12px', color: '#888', marginTop: '4px' }}>
          {item.description}
        </div>
      )}
      {item.price && (
        <div style={{ fontSize: '12px', color: '#666', marginTop: '6px' }}>
          ⏱ {item.duration}
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
            color: '#b7791f',
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
            const photoIds = selectedVenue?.technician_photos?.[code] || []
            return (
              <button
                key={code}
                type="button"
                onClick={() => setSelectedTechnicianCode(code)}
                aria-label={`查看技师 ${code} 的详情${photoIds.length > 0 ? `，${photoIds.length}张照片` : '，暂无照片'}`}
                style={{
                  display: 'flex',
                  flex: '0 0 24px',
                  width: '24px',
                  height: '24px',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: 0,
                  overflow: 'hidden',
                  border: photoIds.length > 0
                    ? '1px solid rgba(60, 45, 35, 0.12)'
                    : '1px solid #dcebe2',
                  borderRadius: '50%',
                  backgroundColor: photoIds.length > 0 ? '#e9e2d9' : '#eaf4ee',
                  backgroundImage: photoIds.length > 0
                    ? `linear-gradient(rgba(20, 20, 20, 0.25), rgba(20, 20, 20, 0.25)), url("${API_BASE_URL}/photos/${photoIds[0]}/image")`
                    : 'linear-gradient(145deg, #f1f8f3, #e4f0e8)',
                  backgroundPosition: 'center',
                  backgroundSize: 'cover',
                  color: photoIds.length > 0 ? '#fff' : '#36734f',
                  fontSize: '8px',
                  fontWeight: 700,
                  textShadow: photoIds.length > 0 ? '0 1px 2px rgba(0, 0, 0, 0.7)' : 'none',
                  cursor: 'pointer',
                  boxShadow: '0 1px 3px rgba(40, 65, 48, 0.08)'
                }}
              >
                <span>{code}</span>
              </button>
            )
            })}
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: '#faf6f0',
      padding: '12px',
      boxSizing: 'border-box'
    }}>
      {/* 头部区域 */}
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 20,
        padding: '5px 12px 9px',
        margin: '-12px -12px 12px',
        background: 'rgba(250, 246, 240, 0.97)',
        backdropFilter: 'blur(12px)',
        borderBottom: '1px solid rgba(158, 132, 101, 0.12)'
      }}>
        <div style={{
          display: 'grid',
          gap: '7px'
        }}>
            <div style={{
              padding: '3px 2px 2px'
            }}>
              <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '8px'
            }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '7px', minWidth: 0 }}>
              <div aria-hidden="true" style={{
                display: 'grid',
                placeItems: 'center',
                flex: '0 0 27px',
                width: '27px',
                height: '27px',
                borderRadius: '9px',
                background: 'rgba(170, 126, 76, 0.12)',
                color: '#8b633c',
                fontSize: '14px',
                fontWeight: 700
              }}>
                昊
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{
                  fontSize: '12px',
                  lineHeight: 1.3,
                  fontWeight: 700,
                  color: '#67472e',
                  whiteSpace: 'nowrap'
                }}>
                  此网页由西港昊天提供
                </div>
              </div>
            </div>
            <a
              href="https://t.me/haotian99988866"
              target="_blank"
              rel="noreferrer"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                flex: '0 0 auto',
                maxWidth: '58%',
                padding: '5px 2px',
                border: 0,
                borderRadius: '20px',
                background: 'transparent',
                color: '#28734d',
                fontSize: '10px',
                lineHeight: 1.2,
                whiteSpace: 'nowrap'
              }}
            >
              <span aria-hidden="true">✈</span>
              @haotian99988866
            </a>
          </div>
          </div>
          <div style={{
            padding: '0 2px'
          }}>
            <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            flexWrap: 'nowrap'
          }}>
            <select
              value={selectedId}
              onChange={e => setSelectedId(e.target.value)}
              aria-label="选择会所"
              style={{
                flex: '1 1 0',
                minWidth: '0',
                height: '36px',
                padding: '0 9px',
                border: '1px solid rgba(174, 151, 122, 0.28)',
                borderRadius: '10px',
                color: '#3f352a',
                fontSize: '12px',
                outline: 'none',
                background: 'rgba(255, 255, 255, 0.62)'
              }}
            >
              {loading ? (
                <option value="">加载中...</option>
              ) : error ? (
                <option value="">{error}</option>
              ) : (
                list.map(v => (
                  <option key={v.id} value={String(v.id)}>
                    🏠 {v.name}
                  </option>
                ))
              )}
            </select>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              flex: '0 0 auto',
              minWidth: '104px',
              height: '36px',
              padding: '0 8px',
              background: 'rgba(56, 131, 93, 0.1)',
              color: '#28734d',
              border: '1px solid rgba(56, 131, 93, 0.16)',
              borderRadius: '10px',
              fontSize: '11px',
              fontWeight: 600,
              whiteSpace: 'nowrap'
            }}>
              <span aria-hidden="true">✓</span>
              今日出勤 <strong style={{ fontSize: '15px' }}>{onDutyCount}</strong> 人
            </div>
          </div>
          </div>
        </div>
      </div>

      <section
        aria-label="公告"
        style={{
          marginBottom: '16px',
          padding: '14px 16px',
          background: '#fff8e8',
          border: '1px solid #f0dfb7',
          borderRadius: '12px'
        }}
      >
        <h2 style={{
          margin: '0 0 6px',
          fontSize: '14px',
          color: '#9a6b18'
        }}>
          📢 公告
        </h2>
        <p style={{
          margin: 0,
          fontSize: '13px',
          color: '#987f54'
        }}>
          暂无公告
        </p>
      </section>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          加载中...
        </div>
      ) : error ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#c53030' }}>
          {error}
        </div>
      ) : list.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          <div style={{ fontSize: '40px', marginBottom: '12px' }}>🔍</div>
          <p>暂无会所数据</p>
        </div>
      ) : !selectedVenue ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          加载中...
        </div>
      ) : (
        <div>
          {/* 今日在岗技师 / 未发布提示 */}
          {technicianList.length > 0 ? (
            <div style={{
              marginBottom: '20px',
              background: '#fff',
              borderRadius: '12px',
              padding: '16px'
            }}>
              <h3 style={{
                fontSize: '15px',
                color: '#2f855a',
                margin: '0 0 12px 0',
                fontWeight: 600
              }}>
                👥 今日在岗技师 ({technicianList.length}人)
                {dutyShiftToday && (
                  <span style={{ fontSize: '12px', color: '#666', marginLeft: '8px' }}>
                    （{dutyShiftToday}）
                  </span>
                )}
              </h3>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(52px, 1fr))',
                gap: '8px'
              }}>
                {technicianList.map(code => {
                  const photoIds = selectedVenue.technician_photos?.[code] || []
                  return (
                    <button
                      key={code}
                      type="button"
                      onClick={() => setSelectedTechnicianCode(code)}
                      aria-label={`查看技师 ${code} 的详情${photoIds.length > 0 ? `，${photoIds.length}张照片` : '，暂无照片'}`}
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
                        border: photoIds.length > 0
                          ? '1px solid rgba(60, 45, 35, 0.12)'
                          : '1px solid #dcebe2',
                        borderRadius: '12px',
                        backgroundColor: photoIds.length > 0 ? '#e9e2d9' : '#eaf4ee',
                        backgroundImage: photoIds.length > 0
                          ? `linear-gradient(to top, rgba(20, 20, 20, 0.72), rgba(20, 20, 20, 0.04) 58%), url("${API_BASE_URL}/photos/${photoIds[0]}/image")`
                          : 'linear-gradient(145deg, #f1f8f3, #e4f0e8)',
                        backgroundPosition: 'center',
                        backgroundSize: 'cover',
                        color: photoIds.length > 0 ? '#fff' : '#36734f',
                        fontSize: '14px',
                        fontWeight: 700,
                        textShadow: photoIds.length > 0 ? '0 1px 3px rgba(0, 0, 0, 0.5)' : 'none',
                        cursor: 'pointer',
                        boxShadow: '0 2px 5px rgba(40, 65, 48, 0.08)'
                      }}
                    >
                      <span style={{ position: 'relative', zIndex: 1 }}>{code}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          ) : (
            <div style={{
              marginBottom: '20px',
              background: '#fff9e6',
              borderRadius: '12px',
              padding: '20px 16px',
              textAlign: 'center',
              color: '#b7791f'
            }}>
              ⚠️ 今日出勤尚未发布
            </div>
          )}

          {/* Services grouped by category */}
          {Object.keys(serviceGroups).length > 0 && (
            <div style={{ marginBottom: '24px' }}>
              {[
                { key: 'small', title: '⭐️ 小项' },
                { key: 'medium', title: '⭐️ 中项' },
                { key: 'high', title: '⭐️ 高项' },
                { key: 'tcm', title: '🤩 中医按摩' },
                { key: 'pedicure', title: '🌸 修脚师' }
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
                style={{ margin: 0, fontSize: '20px', color: '#2c7a56' }}
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

            <h3 style={{ margin: '0 0 10px', fontSize: '15px', color: '#444' }}>
              技师照片
            </h3>
            {(selectedVenue.technician_photos?.[selectedTechnicianCode] || []).length > 0 ? (
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
                      src={`${API_BASE_URL}/photos/${photoId}/image`}
                      alt={`工号 ${selectedTechnicianCode} 的第 ${index + 1} 张照片`}
                      loading="lazy"
                      style={{
                        display: 'block',
                        width: 'min(48vw, 220px)',
                        height: '280px',
                        objectFit: 'cover'
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

            <div style={{ marginBottom: '16px' }}>
              <h3 style={{ margin: '0 0 6px', fontSize: '15px', color: '#444' }}>
                今日出勤
              </h3>
              <div style={{ fontSize: '14px', color: '#666' }}>
                {technicianList.includes(selectedTechnicianCode)
                  ? `在岗${dutyShiftToday ? ` · ${dutyShiftToday}` : ''}`
                  : '不在今日出勤名单'}
              </div>
            </div>

            <div>
              <h3 style={{ margin: '0 0 8px', fontSize: '15px', color: '#444' }}>
                可服务类别
              </h3>
              {technicianServiceCategories.length > 0 ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {technicianServiceCategories.map(category => (
                    <span
                      key={category}
                      style={{
                        padding: '6px 11px',
                        borderRadius: '16px',
                        background: '#f0f7f4',
                        color: '#2c7a56',
                        fontSize: '13px'
                      }}
                    >
                      {category}
                    </span>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: '14px', color: '#888' }}>暂无类别信息</div>
              )}
            </div>

            <a
              href={contactManagerUrl}
              target="_blank"
              rel="noreferrer"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '8px',
                minHeight: '46px',
                marginTop: '20px',
                borderRadius: '12px',
                background: 'linear-gradient(135deg, #38835d, #4a9870)',
                color: '#fff',
                fontSize: '15px',
                fontWeight: 700,
                textDecoration: 'none'
              }}
            >
              <span aria-hidden="true">✈</span>
              联系业务经理昊天
            </a>
            <div style={{
              marginTop: '7px',
              color: '#888',
              fontSize: '12px',
              textAlign: 'center'
            }}>
              将在 Telegram 中预填技师信息和频道照片原帖链接
            </div>
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
              src={`${API_BASE_URL}/photos/${technicianPhotos[photoViewerIndex]}/image${photoRetry ? `?retry=${photoRetry}` : ''}`}
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
                style={photoViewerButtonStyle}
              >
                ‹
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
                style={photoViewerButtonStyle}
              >
                ›
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}