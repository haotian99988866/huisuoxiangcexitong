import { useEffect, useState, useMemo } from 'react'
import { API_BASE_URL, sendData } from '../utils/twa'

export default function VenueList() {
  const [list, setList] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [expandedTechnicianCode, setExpandedTechnicianCode] = useState('')
  const [searchTag, setSearchTag] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setExpandedTechnicianCode('')
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

  const filteredOptions = useMemo(() => {
    if (!searchTag.trim()) return list
    const kw = searchTag.trim().toLowerCase()
    return list.filter(v => {
      if (v.name?.toLowerCase().includes(kw)) return true
      const tags = Array.isArray(v.search_tags) ? v.search_tags : []
      if (tags.some(t => String(t).toLowerCase().includes(kw))) return true
      if (v.address?.toLowerCase().includes(kw)) return true
      return false
    })
  }, [list, searchTag])

  const selectedVenue = useMemo(() => {
    const target = filteredOptions.find(v => String(v.id) === selectedId)
    if (!target && filteredOptions.length > 0) {
      setSelectedId(String(filteredOptions[0].id))
      return filteredOptions[0]
    }
    return target
  }, [filteredOptions, selectedId])

  const onDutyCount = selectedVenue?.on_duty_count ?? 0
  const technicianList = selectedVenue?.technician_list || []
  const dutyShiftToday = selectedVenue?.duty_shift_today
  const serviceItems = selectedVenue?.service_items || []

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

  return (
    <div style={{
      minHeight: '100vh',
      background: '#faf6f0',
      padding: '12px',
      boxSizing: 'border-box'
    }}>
      {/* 头部区域 */}
      <div style={{
        background: '#fff',
        borderRadius: '16px',
        padding: '16px',
        marginBottom: '16px',
        boxShadow: '0 2px 16px rgba(212, 165, 116, 0.1)'
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          marginBottom: '14px',
          flexWrap: 'nowrap'
        }}>
          <select
            value={selectedId}
            onChange={e => setSelectedId(e.target.value)}
            style={{
              flex: '1 1 0',
              minWidth: '0',
              padding: '10px 14px',
              border: '1px solid #e8dcc8',
              borderRadius: '12px',
              fontSize: '15px',
              outline: 'none',
              background: '#fff'
            }}
          >
            {loading ? (
              <option value="">加载中...</option>
            ) : error ? (
              <option value="">{error}</option>
            ) : filteredOptions.length === 0 ? (
              <option value="">无匹配会所</option>
            ) : (
              filteredOptions.map(v => (
                <option key={v.id} value={String(v.id)}>
                  🏠 {v.name}
                </option>
              ))
            )}
          </select>
          <div style={{
            flexShrink: 0,
            padding: '10px 16px',
            background: 'linear-gradient(135deg, #2f855a, #38a169)',
            color: '#fff',
            borderRadius: '20px',
            fontSize: '14px',
            fontWeight: 600,
            whiteSpace: 'nowrap'
          }}>
            ✅ 今日出勤：{onDutyCount} 人
          </div>
        </div>
        <input
          type="text"
          placeholder="🔍 筛选下拉选项..."
          value={searchTag}
          onChange={e => setSearchTag(e.target.value)}
          style={{
            width: '100%',
            padding: '12px 16px',
            border: '1px solid #e8dcc8',
            borderRadius: '12px',
            fontSize: '15px',
            outline: 'none',
            background: '#fefaf5',
            boxSizing: 'border-box'
          }}
        />
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          加载中...
        </div>
      ) : error ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#c53030' }}>
          {error}
        </div>
      ) : filteredOptions.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          <div style={{ fontSize: '40px', marginBottom: '12px' }}>🔍</div>
          <p>未找到匹配会所</p>
        </div>
      ) : !selectedVenue ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          加载中...
        </div>
      ) : (
        <div>
          {/* 封面照片 */}
          {selectedVenue.cover_image && (
            <div style={{ marginBottom: '16px', borderRadius: '12px', overflow: 'hidden' }}>
              <img
                src={selectedVenue.cover_image}
                alt={selectedVenue.name}
                style={{ width: '100%', height: 'auto', display: 'block' }}
              />
            </div>
          )}

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
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {technicianList.map(code => {
                  const photoIds = selectedVenue.technician_photos?.[code] || []
                  const expanded = expandedTechnicianCode === code
                  return (
                    <button
                      key={code}
                      type="button"
                      onClick={() => setExpandedTechnicianCode(expanded ? '' : code)}
                      aria-expanded={expanded}
                      style={{
                        padding: '6px 12px',
                        border: 0,
                        background: expanded ? '#d8eee4' : '#f0f7f4',
                        borderRadius: '16px',
                        fontSize: '14px',
                        color: '#2c7a56',
                        cursor: 'pointer'
                      }}
                    >
                      {code}{photoIds.length > 0 ? ` · ${photoIds.length}张照片` : ''}
                    </button>
                  )
                })}
              </div>
              {expandedTechnicianCode && (
                <div style={{ marginTop: '12px' }}>
                  {(selectedVenue.technician_photos?.[expandedTechnicianCode] || []).length > 0 ? (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                      {selectedVenue.technician_photos[expandedTechnicianCode].map(photoId => (
                        <img
                          key={photoId}
                          src={`${API_BASE_URL}/photos/${photoId}/image`}
                          alt={`工号 ${expandedTechnicianCode} 的照片`}
                          loading="lazy"
                          style={{
                            width: '96px',
                            height: '120px',
                            objectFit: 'cover',
                            borderRadius: '8px',
                            background: '#f2f2f2'
                          }}
                        />
                      ))}
                    </div>
                  ) : (
                    <div style={{ fontSize: '13px', color: '#888' }}>
                      工号 {expandedTechnicianCode} 暂无已确认照片
                    </div>
                  )}
                </div>
              )}
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

          {/* 服务项目 - 按固定顺序展示 */}
          {Object.keys(serviceGroups).length > 0 && (
            <div style={{ marginBottom: '24px' }}>
              {/* 小项 */}
              {serviceGroups.small && (
                <div style={{ marginBottom: '18px' }}>
                  <h3 style={{
                    fontSize: '15px',
                    color: '#b7791f',
                    margin: '0 0 10px 0',
                    fontWeight: 600
                  }}>
                    ⭐️ 小项
                  </h3>
                  <div style={{ display: 'grid', gap: '8px' }}>
                    {serviceGroups.small.items.map(item => (
                      <div key={item.id} style={{
                        background: '#fff',
                        borderRadius: '10px',
                        padding: '14px 16px'
                      }}>
                        <div style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'flex-start'
                        }}>
                          <div style={{ fontSize: '14px', fontWeight: 600, color: '#333' }}>
                            {item.name}
                          </div>
                          <div style={{
                            fontSize: '15px',
                            fontWeight: 700,
                            color: '#c53030',
                            flexShrink: 0,
                            marginLeft: '8px'
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
                    ))}
                  </div>
                </div>
              )}

              {/* 中项 */}
              {serviceGroups.medium && (
                <div style={{ marginBottom: '18px' }}>
                  <h3 style={{
                    fontSize: '15px',
                    color: '#b7791f',
                    margin: '0 0 10px 0',
                    fontWeight: 600
                  }}>
                    ⭐️ 中项
                  </h3>
                  <div style={{ display: 'grid', gap: '8px' }}>
                    {serviceGroups.medium.items.map(item => (
                      <div key={item.id} style={{
                        background: '#fff',
                        borderRadius: '10px',
                        padding: '14px 16px'
                      }}>
                        <div style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'flex-start'
                        }}>
                          <div style={{ fontSize: '14px', fontWeight: 600, color: '#333' }}>
                            {item.name}
                          </div>
                          <div style={{
                            fontSize: '15px',
                            fontWeight: 700,
                            color: '#c53030',
                            flexShrink: 0,
                            marginLeft: '8px'
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
                    ))}
                  </div>
                </div>
              )}

              {/* 高项 */}
              {serviceGroups.high && (
                <div style={{ marginBottom: '18px' }}>
                  <h3 style={{
                    fontSize: '15px',
                    color: '#b7791f',
                    margin: '0 0 10px 0',
                    fontWeight: 600
                  }}>
                    ⭐️ 高项
                  </h3>
                  <div style={{ display: 'grid', gap: '8px' }}>
                    {serviceGroups.high.items.map(item => (
                      <div key={item.id} style={{
                        background: '#fff',
                        borderRadius: '10px',
                        padding: '14px 16px'
                      }}>
                        <div style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'flex-start'
                        }}>
                          <div style={{ fontSize: '14px', fontWeight: 600, color: '#333' }}>
                            {item.name}
                          </div>
                          <div style={{
                            fontSize: '15px',
                            fontWeight: 700,
                            color: '#c53030',
                            flexShrink: 0,
                            marginLeft: '8px'
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
                    ))}
                  </div>
                </div>
              )}

              {/* 中医/按摩 */}
              {serviceGroups.tcm && (
                <div style={{ marginBottom: '18px' }}>
                  <h3 style={{
                    fontSize: '15px',
                    color: '#b7791f',
                    margin: '0 0 10px 0',
                    fontWeight: 600
                  }}>
                    🤩 中医按摩
                  </h3>
                  <div style={{ display: 'grid', gap: '8px' }}>
                    {serviceGroups.tcm.items.map(item => (
                      <div key={item.id} style={{
                        background: '#fff',
                        borderRadius: '10px',
                        padding: '14px 16px'
                      }}>
                        <div style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'flex-start'
                        }}>
                          <div style={{ fontSize: '14px', fontWeight: 600, color: '#333' }}>
                            {item.name}
                          </div>
                          <div style={{
                            fontSize: '15px',
                            fontWeight: 700,
                            color: '#c53030',
                            flexShrink: 0,
                            marginLeft: '8px'
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
                    ))}
                  </div>
                </div>
              )}

              {/* 修脚 */}
              {serviceGroups.pedicure && (
                <div style={{ marginBottom: '18px' }}>
                  <h3 style={{
                    fontSize: '15px',
                    color: '#b7791f',
                    margin: '0 0 10px 0',
                    fontWeight: 600
                  }}>
                    🌸 修脚师
                  </h3>
                  <div style={{ display: 'grid', gap: '8px' }}>
                    {serviceGroups.pedicure.items.map(item => (
                      <div key={item.id} style={{
                        background: '#fff',
                        borderRadius: '10px',
                        padding: '14px 16px'
                      }}>
                        <div style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'flex-start'
                        }}>
                          <div style={{ fontSize: '14px', fontWeight: 600, color: '#333' }}>
                            {item.name}
                          </div>
                          <div style={{
                            fontSize: '15px',
                            fontWeight: 700,
                            color: '#c53030',
                            flexShrink: 0,
                            marginLeft: '8px'
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
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}