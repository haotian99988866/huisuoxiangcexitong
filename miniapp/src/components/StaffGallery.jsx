import { useEffect, useMemo, useRef, useState } from 'react'
import MusicDialog from './MusicDialog'
import { API_BASE_URL } from '../utils/twa'
import { contactName, mediaUrl, tenantHeaders } from '../utils/tenant'
import CopyBooking from './CopyBooking'
import { requestAdmin } from '../utils/admin'
import { bookingUrl } from '../utils/booking'
import { useAnalyticsScreen } from '../utils/analytics'
import { money } from '../utils/commerce'
import useCustomerSubscriptions from '../utils/useCustomerSubscriptions'
import { SubscriptionControl, SubscriptionFeedback, SubscribedList } from './CustomerDutyControls'
import LazyMedia from './media/LazyMedia'
import PhotoSkeleton from './media/PhotoSkeleton'
import { ROSTER_DUTY_VENUE_IDS } from '../utils/venueHome'
import {
  boardSortIndex,
  buildCodeBoardMap,
  displayMingxiPrice,
  memberMingxiBoard,
  MINGXI_BOARD_ORDER,
} from '../utils/mingxiBoard'
import './StaffGallery.css'

function attendanceEmptyCopy({
  attendanceOnly,
  rosterAvailable,
  pendingPhotos,
  hasDutyCodes,
  unarchivedCount,
  searchTerm,
  rosterDutyOnly,
}) {
  if (searchTerm) return '没有匹配的工号。'
  if (!attendanceOnly) return '暂无人员。档案随每日出勤与可识别照片自动积累。'
  if (!rosterAvailable && !hasDutyCodes) {
    if (rosterDutyOnly) {
      return '本班出勤初稿尚未同步。请稍后刷新，或先查看人员相册。'
    }
    return pendingPhotos > 0
      ? '本班名单尚未从频道同步，已有照片待核对。请稍后刷新，或先查看人员相册。'
      : '本班名单尚未从频道同步。请稍后刷新，或先查看人员相册。'
  }
  if (!rosterDutyOnly && pendingPhotos > 0) {
    return '本班名单已有，照片仍在核对中。可先查看人员相册，或稍后刷新。'
  }
  if (unarchivedCount > 0) {
    return `本班名单已同步（${unarchivedCount}人尚无公开档案照片）。可先查看人员相册。`
  }
  return '当前没有可展示的本班出勤档案。'
}

function cardOverlayLabel({ attendanceOnly, dutyPhoto, dutyText, rosterDutyOnly }) {
  if (attendanceOnly) {
    if (rosterDutyOnly) return null
    return dutyPhoto ? '本班' : '待更新'
  }
  return dutyText
}

async function requestStaff(action, fields, signal) {
  const response = await fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify({ action, ...fields }), cache: 'no-store', signal,
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '人员档案读取失败')
  return result
}

function memberCover(member, venue, rosterDutyOnly, attendanceOnly) {
  const dutyPhoto = !rosterDutyOnly && attendanceOnly
    && venue.technician_photos?.[member.code]?.[0]
  if (member.cover) return { id: member.cover, kind: 'staff' }
  if (dutyPhoto) return { id: dutyPhoto, kind: 'duty' }
  return { id: null, kind: null }
}

function cardPriceLabel(member) {
  const prices90 = member.price_options?.filter(option => option.minutes === 90) || []
  if (prices90.length) {
    const lowest = Math.min(...prices90.map(option => Number(option.price_usd)))
    return `$${displayMingxiPrice(lowest)}`
  }
  if (member.price_usd != null && member.price_detail?.includes('90分钟')) {
    return `$${displayMingxiPrice(member.price_usd)}`
  }
  if (member.price_usd != null) return `$${displayMingxiPrice(member.price_usd)}起`
  return '价待确认'
}

export default function StaffGallery({ venue, admin = false, onSelect, search = '', selectedId = null, allowSkip = false, photoFirst = false, attendanceOnly = false, onBrowseAlbum, openStaffId = null }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [selected, setSelected] = useState(null)
  const [imageIndex, setImageIndex] = useState(0)
  const [replacement, setReplacement] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [description, setDescription] = useState('')
  const [boardFilter, setBoardFilter] = useState('全部')
  const touchStart = useRef(null)
  // 订阅只改本地状态，勿触发整表重拉，避免屏幕一闪
  const subscription = useCustomerSubscriptions(venue.id, null, { enabled: !admin })
  const customerDuty = selected ? subscription.states?.[selected.id] : null
  const isMingxi = Number(venue.id) === 2
  const dutyLabel = member => {
    const state = !admin && subscription.states?.[member.id]
    return state ? state.published_at ? `${state.shift}出勤` : '本班未发布出勤'
      : member.attendance_overlay || member.duty_label || '出勤记录待确认'
  }
  useAnalyticsScreen(selected ? 'technician_detail' : null)

  useEffect(() => {
    setData(null)
    setSelected(null)
    setReplacement(null)
    setNotice('')
    setBoardFilter('全部')
  }, [venue.id])
  useEffect(() => {
    setBoardFilter('全部')
  }, [attendanceOnly])
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 15000)
    setError('')
    // 刷新时保留旧列表，避免先清空再加载造成整屏闪白
    const fields = { venue_id: venue.id, mode: 'all' }
    const request = admin ? requestAdmin('admin_staff', fields) : requestStaff('get_staff', fields, controller.signal)
    request.then(result => { if (active) setData(result) }).catch(failure => {
      if (active) {
        console.error('[Staff gallery]', failure)
        setError(failure.name === 'AbortError' ? '人员档案读取超时，请重试' : failure.message)
      }
    }).finally(() => clearTimeout(timeout))
    return () => { active = false; clearTimeout(timeout); controller.abort() }
  }, [venue.id, attempt, admin])
  useEffect(() => {
    if (!data?.members?.length || !openStaffId) return
    const target = data.members.find(member => String(member.id) === String(openStaffId))
    if (target) setSelected(target)
  }, [data, openStaffId])
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && !selected && !replacement && !busy) setAttempt(value => value + 1)
    }, 60000)
    return () => clearInterval(timer)
  }, [selected, replacement, busy])
  useEffect(() => {
    setDetail(null)
    setDetailError('')
    setDescription('')
    setImageIndex(0)
    if (!selected) return
    let active = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    setDetailLoading(true)
    const request = admin
      ? requestAdmin('admin_staff_detail', { staff_id: selected.id })
      : requestStaff('get_staff_detail', { staff_id: selected.id }, controller.signal)
    request.then(result => {
      if (active) {
        setDetail(result)
        if (admin) setDescription(result.description)
      }
    })
      .catch(failure => {
        if (active) {
          console.error('[Staff detail]', failure)
          setDetailError(failure.name === 'AbortError' ? '相册读取超时，请关闭后重试' : failure.message)
        }
      }).finally(() => {
        clearTimeout(timer)
        if (active) setDetailLoading(false)
      })
    return () => { active = false; clearTimeout(timer); controller.abort() }
  }, [selected, admin])

  const changePerson = async () => {
    if (busy || !replacement) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await requestAdmin('admin_staff_replace', { staff_id: replacement.id, confirmed: true })
      setNotice('已建立新人独立档案，旧照片和历史归属保留。接下来发布的出勤和照片归入新人。')
      setReplacement(null)
      setAttempt(value => value + 1)
    } catch (failure) {
      console.error('[Staff replacement]', failure)
      setError(`${failure.message}；结果未确认时请先刷新核对，勿再次对新人执行换人。`)
    } finally { setBusy(false) }
  }
  const photo = detail?.photos[imageIndex]
  const ordinaryMassage = venue.id === 1
  const changed = admin && detail && description !== detail.description
  const searchTerm = search.trim().toLocaleLowerCase()
  const rosterDutyOnly = ROSTER_DUTY_VENUE_IDS.includes(Number(venue.id))
  const technicianList = venue.technician_list || []
  const attendanceCodes = useMemo(
    () => new Set(technicianList.map(String)),
    [technicianList],
  )
  const isOnDuty = member => rosterDutyOnly
    ? attendanceCodes.has(String(member.code)) : Boolean(member.duty_label)
  const shiftName = (() => {
    const preferred = data?.shift || venue.current_shift
    const raw = preferred || venue.duty_shift_today || '本班'
    if (raw === '早班') return '白班'
    if (raw === '实时出勤') return preferred || '本班'
    return raw
  })()
  const codeBoardMap = useMemo(
    () => buildCodeBoardMap(venue.attendance_groups),
    [venue.attendance_groups],
  )
  const baseMembers = useMemo(() => {
    const list = (data?.members || []).filter(member => {
      const onDuty = rosterDutyOnly
        ? attendanceCodes.has(String(member.code))
        : Boolean(member.duty_label)
      return (!attendanceOnly || onDuty)
        && (!searchTerm || String(member.code).toLocaleLowerCase().includes(searchTerm))
    })
    if (!isMingxi || !photoFirst) return list
    return [...list].sort((left, right) => {
      const leftCover = memberCover(left, venue, rosterDutyOnly, attendanceOnly).id ? 0 : 1
      const rightCover = memberCover(right, venue, rosterDutyOnly, attendanceOnly).id ? 0 : 1
      if (leftCover !== rightCover) return leftCover - rightCover
      return boardSortIndex(memberMingxiBoard(left, codeBoardMap))
        - boardSortIndex(memberMingxiBoard(right, codeBoardMap))
    })
  }, [data, attendanceOnly, searchTerm, isMingxi, photoFirst, venue, rosterDutyOnly, attendanceCodes, codeBoardMap])
  const boardChips = useMemo(() => {
    if (!isMingxi || !photoFirst) return []
    const counts = new Map()
    for (const member of baseMembers) {
      const board = memberMingxiBoard(member, codeBoardMap)
      counts.set(board, (counts.get(board) || 0) + 1)
    }
    const ordered = MINGXI_BOARD_ORDER.filter(name => counts.has(name))
      .map(name => ({ name, count: counts.get(name) }))
    for (const [name, count] of counts) {
      if (!MINGXI_BOARD_ORDER.includes(name)) ordered.push({ name, count })
    }
    return ordered
  }, [baseMembers, isMingxi, photoFirst, codeBoardMap])
  const visibleMembers = useMemo(() => {
    if (!isMingxi || !photoFirst || boardFilter === '全部') return baseMembers
    return baseMembers.filter(member => memberMingxiBoard(member, codeBoardMap) === boardFilter)
  }, [baseMembers, isMingxi, photoFirst, boardFilter, codeBoardMap])
  const unarchivedCodes = attendanceOnly && data
    ? [...attendanceCodes].filter(code => !data.members.some(member => String(member.code) === code)) : []
  const selectedBoard = selected ? memberMingxiBoard(selected, codeBoardMap) : null
  const closeDetail = () => {
    if (busy) return
    if (changed && !window.confirm('说明尚未提交，确定放弃修改并关闭吗？')) return
    setSelected(null)
  }
  const saveDescription = async () => {
    if (busy || !changed || !window.confirm('确认提交本次说明修改？说明仅后台管理员可见。')) return
    setBusy(true)
    setDetailError('')
    setNotice('')
    try {
      const result = await requestAdmin('admin_staff_description', {
        staff_id: selected.id, description, description_revision: detail.description_revision, confirmed: true,
      })
      setDetail(current => ({ ...current, description: result.description, description_revision: result.description_revision }))
      setDescription(result.description)
      setNotice('说明已保存，仅后台管理员可见。')
    } catch (failure) {
      console.error('[Staff description]', failure)
      setDetailError(failure.message)
    } finally { setBusy(false) }
  }
  const deleteArchive = async () => {
    if (busy || !detail || !window.confirm(
      `确认删除工号 ${selected.code} 的档案 #${selected.id}？后台和客人端将隐藏该档案，说明会清除；频道消息和历史出勤保留。此操作不可撤销。`,
    )) return
    setBusy(true)
    setDetailError('')
    setNotice('')
    try {
      await requestAdmin('admin_staff_delete', { staff_id: selected.id, confirmed: true })
      setSelected(null)
      setNotice('档案已删除，后台和客人端均已隐藏，频道消息和历史出勤保留。')
      setAttempt(value => value + 1)
    } catch (failure) {
      console.error('[Staff deletion]', failure)
      setDetailError(failure.message)
    } finally { setBusy(false) }
  }

  const galleryClass = [
    'staff-gallery',
    photoFirst ? 'mingxi-photo-gallery' : '',
    photoFirst && isMingxi && attendanceOnly ? 'mingxi-duty-grid' : '',
    photoFirst && isMingxi && !attendanceOnly ? 'mingxi-album-grid' : '',
  ].filter(Boolean).join(' ')

  return <section className={galleryClass} aria-label={`${venue.name}${photoFirst ? attendanceOnly ? '出勤照片' : '人员相册' : '人员档案'}`}>
    <div className="staff-heading">
      <h2>{photoFirst ? <>{attendanceOnly ? shiftName : '相册'}
        {data && <span className="mingxi-personnel-count"> · {attendanceOnly ? visibleMembers.length : data.total}位</span>}</> : '人员档案'}</h2>
      {photoFirst && !isMingxi && <span className="staff-heading-note">{attendanceOnly ? '发布不代表实时在岗' : '点击照片查看相册'}</span>}
      {photoFirst && isMingxi && !attendanceOnly && <span className="staff-heading-note">订阅出勤提醒</span>}
      <button type="button" disabled={busy} onClick={() => setAttempt(value => value + 1)}>刷新</button>
    </div>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {notice && <p className="membership-notice" role="status">{notice}</p>}
    {!data && !error && (
      <PhotoSkeleton
        count={isMingxi && photoFirst ? 9 : 6}
        columns={isMingxi && photoFirst ? 3 : 2}
        dense={isMingxi && photoFirst}
        chips={isMingxi && photoFirst}
        label="正在读取人员档案…"
      />
    )}
    {data && <>
      {!photoFirst && <p className="staff-meta">{`全部人员 · ${data.total}位 · ${data.shift}`}</p>}
      {!admin && ((isMingxi && photoFirst && !attendanceOnly) || !photoFirst) && <>
        <p className="staff-meta">{subscription.authenticated
          ? '未出勤技师可点「订阅出勤」；已订阅的人卡片会标「已订阅」，也可在上方清单取消。'
          : '请从 Telegram 主入口打开，才能订阅出勤提醒。'}</p>
        <SubscriptionFeedback subscription={subscription} />
        {isMingxi && photoFirst && !attendanceOnly && (
          <SubscribedList members={data.members} subscription={subscription} />
        )}
      </>}
      {!data.roster_available && !attendanceCodes.size && <p className="staff-meta">{attendanceOnly
        ? (rosterDutyOnly
          ? '本班出勤初稿尚未同步。'
          : ((data.pending_photo_count || 0) > 0
            ? '本班名单尚未同步，频道已有照片待核对。'
            : '本班名单尚未从频道同步。'))
        : '当前班次尚未发布出勤，照片仍可查看；未出勤天数依据最近发布记录。'}</p>}
      {isMingxi && photoFirst && boardChips.length > 0 && (
        <div className="mingxi-board-chips" role="toolbar" aria-label="按牌筛选">
          <button type="button" aria-pressed={boardFilter === '全部'}
            onClick={() => setBoardFilter('全部')}>全部 · {baseMembers.length}</button>
          {boardChips.map(chip => (
            <button key={chip.name} type="button" aria-pressed={boardFilter === chip.name}
              onClick={() => setBoardFilter(chip.name)}>{chip.name} · {chip.count}</button>
          ))}
        </div>
      )}
      {onSelect && allowSkip && <button type="button" className="staff-skip"
        onClick={() => onSelect(null)}>暂不关联技师，后续可补录</button>}
      {!visibleMembers.length && (() => {
        const emptyText = attendanceEmptyCopy({
          attendanceOnly,
          rosterAvailable: Boolean(data.roster_available),
          pendingPhotos: data.pending_photo_count || 0,
          hasDutyCodes: attendanceCodes.size > 0,
          unarchivedCount: unarchivedCodes.length,
          searchTerm,
          rosterDutyOnly,
        })
        return attendanceOnly && onBrowseAlbum && !searchTerm ? (
          <div className="staff-empty-cta" role="status">
            <p>{boardFilter !== '全部' ? `当前牌没有可展示人员。` : emptyText}</p>
            {boardFilter !== '全部'
              ? <button type="button" onClick={() => setBoardFilter('全部')}>查看全部</button>
              : <button type="button" onClick={onBrowseAlbum}>查看人员相册</button>}
          </div>
        ) : <p className="staff-meta">{boardFilter !== '全部' ? '当前牌没有可展示人员。' : emptyText}</p>
      })()}
      <div className="staff-grid">
        {visibleMembers.map(member => {
          const cover = memberCover(member, venue, rosterDutyOnly, attendanceOnly)
          const board = isMingxi && photoFirst ? memberMingxiBoard(member, codeBoardMap) : null
          const overlay = cardOverlayLabel({
            attendanceOnly,
            dutyPhoto: cover.kind === 'duty',
            dutyText: rosterDutyOnly && attendanceOnly ? null : dutyLabel(member),
            rosterDutyOnly,
          })
          const subState = subscription.states?.[member.id]
          const isSubscribed = Boolean(subState?.subscribed)
          const onDuty = rosterDutyOnly && attendanceOnly
            ? true
            : Boolean(!admin && subState
              ? subState.published_at : member.duty_label)
          return <article className={`staff-card${isSubscribed ? ' is-subscribed' : ''}`} key={member.id}>
          <button type="button" className="staff-open" onClick={() => setSelected(member)}>
            <div className="staff-cover">
              {cover.id && !member.blocked
                ? <LazyMedia src={mediaUrl(`${API_BASE_URL}/${cover.kind === 'duty' ? 'photos' : 'staff-photos'}/${cover.id}/image?thumb=1`)}
                  alt={`工号 ${member.code}`} />
                : <span>{member.blocked ? '暂不展示' : '照片待更新'}</span>}
              {overlay && <span className={`staff-attendance-overlay${onDuty ? ' is-on-duty' : ''}`}>
                {overlay}
              </span>}
              {board && <span className="mingxi-board-badge">{board}</span>}
              {isSubscribed && <span className="mingxi-subscribed-badge">已订阅</span>}
            </div>
            <div className="staff-card-info">
              {isMingxi && photoFirst ? <>
                <strong>{member.code}{board ? ` · ${board}` : ''}</strong>
                <span className="mingxi-staff-price">{cardPriceLabel(member)}</span>
              </> : <>
                <strong>工号 {member.code}</strong>
                {photoFirst && <span className="mingxi-staff-price">{cardPriceLabel(member)} / 90分钟</span>}
                {!photoFirst && <small>{`${member.photo_count}张照片`}</small>}
              </>}
            </div>
          </button>
          {!admin && (!photoFirst || (isMingxi && !attendanceOnly)) && (
            <SubscriptionControl
              compact={isMingxi && photoFirst}
              id={member.id}
              code={member.code}
              onDuty={Boolean(member.duty_label)}
              subscription={subscription}
            />
          )}
          {onSelect && <button type="button" className="staff-replace" disabled={busy || member.blocked}
            aria-pressed={selectedId === member.id} onClick={() => onSelect(member)}>
            {selectedId === member.id ? '已选择此技师' : '选择此技师'}
          </button>}
          {admin && !onSelect && <button type="button" className="staff-replace" disabled={busy}
            onClick={() => setReplacement(member)}>此工号已换人</button>}
        </article>})}
      </div>
      {unarchivedCodes.length > 0 && <p className="staff-meta">工号 {unarchivedCodes.join('、')} 尚未建立可展示档案。</p>}
    </>}
    {selected && <MusicDialog className={`${admin ? 'account-sheet staff-sheet staff-admin-detail' : ordinaryMassage ? 'staff-sheet' : 'account-sheet staff-sheet'}${photoFirst ? ' mingxi-photo-sheet' : ''}`}
      title={ordinaryMassage ? `工号 ${selected.code}` : isMingxi && photoFirst
        ? `工号 ${selected.code}${selectedBoard ? ` · ${selectedBoard}` : ''}`
        : `${venue.name} · 工号 ${selected.code}`}
      scrollBody onClose={closeDetail}
      headerContent={admin ? <div className="staff-admin-header-actions">
        <button type="button" className="staff-admin-delete" disabled={busy || !detail || detailLoading}
          onClick={deleteArchive}>删除档案</button>
      </div> : undefined}
      footer={admin ? <div className="staff-admin-footer">
        <button type="button" disabled={busy || !changed} onClick={() => setDescription(detail.description)}>取消修改</button>
        <button type="button" disabled={busy || !changed || description.length > 2000}
          onClick={saveDescription}>{busy ? '处理中…' : '确认并提交'}</button>
      </div> : <div className={`staff-customer-actions${isMingxi && photoFirst ? ' mingxi-detail-actions' : ''}`}>
        {isMingxi && photoFirst && selected.price_options?.length > 0 && (
          <div className="mingxi-detail-prices" aria-label="牌价">
            {selected.price_options.map(option =>
              <p key={`${option.board}-${option.minutes}-${option.label}`}>
                {option.minutes}分钟 · {option.label} ${displayMingxiPrice(option.price_usd)}
              </p>)}
          </div>
        )}
        {!(isMingxi && photoFirst && attendanceOnly) && <SubscriptionFeedback subscription={subscription} />}
        <div className="staff-customer-buttons">
          {!(isMingxi && photoFirst && attendanceOnly) && (
            <SubscriptionControl
              compact={isMingxi && photoFirst}
              id={selected.id}
              code={selected.code}
              onDuty={Boolean(selected.duty_label)}
              subscription={subscription}
            />
          )}
          {(!ordinaryMassage || customerDuty?.published_at || (!customerDuty && selected.duty_label)) &&
            <CopyBooking
              compact={isMingxi && photoFirst}
              label={isMingxi && photoFirst ? `联系${contactName()}` : null}
              key={`${selected.id}-${photo?.id || ''}`}
              url={bookingUrl({
              title: `你好，我想咨询${venue.name} · 工号${selected.code}`,
              details: [
                selectedBoard && `牌：${selectedBoard}`,
                ordinaryMassage && '仅咨询正规按摩服务',
                selected.price_usd != null && `价格：$${money(selected.price_usd)} 起 · ${selected.price_detail}`,
                ...(selected.price_options || []).map(option =>
                  `${option.board} · ${option.minutes}分钟 · ${option.label} $${money(option.price_usd)}`),
              ],
              links: photo ? [new URL(mediaUrl(`${API_BASE_URL}/staff-photos/${photo.id}/image`), window.location.origin).href] : [],
            })} />}
        </div>
        {!(isMingxi && photoFirst) && !photoFirst && <p className="staff-meta">{subscription.authenticated
          ? '持续订阅，白班、晚班每班提醒一次，可随时取消。请先在机器人聊天中点击开始。'
          : '请从 Telegram 主入口打开，才能订阅。'}只提醒订阅后的新发布，不补发历史；出勤发布不代表实时在岗。</p>}
      </div>}>
      <div className="staff-detail-media">
      {detailLoading && (isMingxi && photoFirst
        ? <PhotoSkeleton hero label="正在读取相册…" />
        : <p role="status">正在读取相册…</p>)}
      {detailError && <p className="membership-error" role="alert">{detailError}</p>}
      {detail && !detail.photos.length && !detailLoading && <p>照片待更新</p>}
      {photo && !detailLoading && <>
        {isMingxi && photoFirst ? (
          <div className="mingxi-detail-hero"
            onTouchStart={event => { touchStart.current = event.touches[0]?.clientX ?? null }}
            onTouchEnd={event => {
              const start = touchStart.current
              touchStart.current = null
              if (start === null || !detail?.photos.length) return
              const delta = (event.changedTouches[0]?.clientX ?? start) - start
              if (Math.abs(delta) > 60) setImageIndex(index => Math.max(0, Math.min(detail.photos.length - 1, index + (delta < 0 ? 1 : -1))))
            }}>
            <LazyMedia className="staff-detail-photo" src={mediaUrl(`${API_BASE_URL}/staff-photos/${photo.id}/image`)}
              alt={`工号 ${selected.code} 的第 ${imageIndex + 1} 张照片`} />
            {detail.photos.length > 1 && (
              <span className="mingxi-detail-count">{imageIndex + 1}/{detail.photos.length}</span>
            )}
          </div>
        ) : (
          <LazyMedia className="staff-detail-photo" src={mediaUrl(`${API_BASE_URL}/staff-photos/${photo.id}/image`)}
            onTouchStart={event => { touchStart.current = event.touches[0]?.clientX ?? null }}
            onTouchEnd={event => {
              const start = touchStart.current
              touchStart.current = null
              if (!photoFirst || start === null || !detail?.photos.length) return
              const delta = (event.changedTouches[0]?.clientX ?? start) - start
              if (Math.abs(delta) > 60) setImageIndex(index => Math.max(0, Math.min(detail.photos.length - 1, index + (delta < 0 ? 1 : -1))))
            }}
            alt={`工号 ${selected.code} 的第 ${imageIndex + 1} 张照片`} />
        )}
        {detail.photos.length > 1 && (isMingxi && photoFirst ? (
          <div className="mingxi-thumb-strip" role="tablist" aria-label="相册缩略图">
            {detail.photos.map((item, index) => (
              <button key={item.id} type="button" role="tab" aria-selected={index === imageIndex}
                className={index === imageIndex ? 'is-active' : undefined}
                onClick={() => setImageIndex(index)}>
                <LazyMedia src={mediaUrl(`${API_BASE_URL}/staff-photos/${item.id}/image?thumb=1`)}
                  alt={`第 ${index + 1} 张`} />
              </button>
            ))}
          </div>
        ) : (
          <div className="staff-pages">
            <button type="button" disabled={imageIndex === 0} onClick={() => setImageIndex(value => value - 1)}>上一张</button>
            <span>{imageIndex + 1} / {detail.photos.length}</span>
            <button type="button" disabled={imageIndex + 1 === detail.photos.length} onClick={() => setImageIndex(value => value + 1)}>下一张</button>
          </div>
        ))}
        {!photoFirst && <p className="staff-meta">首次收录：{photo.first_seen}</p>}
      </>}
      </div>
      <div className="staff-detail-summary">
      {ordinaryMassage && !admin ? <><p className="staff-detail-duty">{dutyLabel(selected)}</p>
      {customerDuty?.published_at && <p className="staff-meta">发布时间：{customerDuty.published_at}</p>}
      <p className="staff-meta">仅提供正规按摩信息。已发布不代表实时在岗。</p></> : !(isMingxi && photoFirst) ? <>
      <p className="staff-detail-duty">{dutyLabel(selected)}</p>
      {photoFirst && selected.price_options?.length
        ? <div className="mingxi-detail-prices">{selected.price_options.map(option =>
          <p key={`${option.board}-${option.minutes}`}>{option.board} · {option.minutes}分钟 · {option.label} ${money(option.price_usd)}</p>)}</div>
        : <p>{selected.price_usd === null || selected.price_usd === undefined
          ? '价格待确认' : `$${money(selected.price_usd)} 起 · ${selected.price_detail}`}</p>}
      {!photoFirst && selected.last_duty_at && <p className="staff-meta">最近出勤发布：{selected.last_duty_at}</p>}
      {!photoFirst && !selected.duty_label && <p className="staff-meta">天数按最近出勤发布记录计算，未发布不代表实际休息或离职。</p>}
      {!photoFirst && <p className="staff-meta">档案编号 {selected.id} · 未确认当班出勤不代表休息或离职</p>}
      {photoFirst
        ? <p className="staff-meta">发布不代表实时在岗，实际请联系核实。</p>
        : selected.duty_source === 'photo' && <p className="staff-meta">出勤标签依据会所本班次发布的出勤照片，实际在岗请联系核实。</p>}
      </> : null}
      </div>
      {admin && detail && <div className="staff-admin-description">
        <label htmlFor={`staff-description-${selected.id}`}>添加说明（仅后台可见）</label>
        <textarea id={`staff-description-${selected.id}`} value={description} maxLength={2000}
          disabled={busy} rows={5} onChange={event => setDescription(event.target.value)} />
        <p className="staff-meta">{description.length}/2000{changed ? ' · 尚未提交' : ''}</p>
        {notice && <p className="membership-notice" role="status">{notice}</p>}
      </div>}
    </MusicDialog>}
    {replacement && <MusicDialog className="account-sheet" title="确认工号换人" onClose={() => { if (!busy) setReplacement(null) }}>
      <p>{venue.name} · 工号 {replacement.code} · 原档案 {replacement.id}</p>
      <p>仅在工号实际换成另一个人时操作，不是更新照片。旧档案及相册保留但退出公开展示；新人使用新的独立编号。</p>
      <p>请先确认，再发布新人的出勤及照片；系统不会将已经归档的旧照片自动转移给新人。</p>
      <button type="button" disabled={busy} onClick={changePerson}>{busy ? '保存中…' : '确认换人，建立新人档案'}</button>
    </MusicDialog>}
  </section>
}
