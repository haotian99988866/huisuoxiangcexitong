import { useEffect, useState } from 'react'
import MusicDialog from './MusicDialog'
import { API_BASE_URL, openTelegramLink } from '../utils/twa'
import { mediaUrl, tenantHeaders, contactName, contactUrl } from '../utils/tenant'
import { requestAdmin } from '../utils/admin'
import { bookingUrl } from '../utils/booking'
import { useAnalyticsScreen } from '../utils/analytics'
import { money } from '../utils/commerce'
import useCustomerSubscriptions from '../utils/useCustomerSubscriptions'
import { SubscriptionControl, SubscriptionFeedback } from './CustomerDutyControls'
import './StaffGallery.css'

async function requestStaff(action, fields, signal) {
  const response = await fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify({ action, ...fields }), cache: 'no-store', signal,
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '人员档案读取失败')
  return result
}

export default function StaffGallery({ venue, admin = false, onSelect }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [selected, setSelected] = useState(null)
  const [imageIndex, setImageIndex] = useState(0)
  const [failedImages, setFailedImages] = useState(new Set())
  const [replacement, setReplacement] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [description, setDescription] = useState('')
  const subscription = useCustomerSubscriptions(venue.id, () => setAttempt(value => value + 1), { enabled: !admin })
  const customerDuty = selected ? subscription.states?.[selected.id] : null
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
  }, [venue.id])
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 15000)
    setData(null)
    setError('')
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
    setFailedImages(new Set())
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

  const failImage = id => setFailedImages(current => new Set([...current, id]))
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

  return <section className="staff-gallery" aria-label={`${venue.name}人员档案`}>
    <div className="staff-heading">
      <h2>人员档案</h2>
      <button type="button" disabled={busy} onClick={() => setAttempt(value => value + 1)}>刷新</button>
    </div>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {notice && <p className="membership-notice" role="status">{notice}</p>}
    {!data && !error && <p role="status">正在读取人员档案…</p>}
    {data && <>
      <p className="staff-meta">全部人员 · {data.total}位 · {data.shift}</p>
      {!admin && <>
        <p className="staff-meta">{subscription.authenticated
          ? '可持续订阅未出勤档案，白班、晚班每班新发布出勤时提醒一次，直到取消。'
          : '请从 Telegram 主入口打开，才能订阅出勤提醒。'}</p>
        <SubscriptionFeedback subscription={subscription} />
      </>}
      {!data.roster_available && <p className="staff-meta">当前班次尚未发布出勤，照片仍可查看；未出勤天数依据最近发布记录。</p>}
      {!data.members.length && <p className="staff-meta">暂无人员。档案随每日出勤与可识别照片自动积累。</p>}
      <div className="staff-grid">
        {data.members.map(member => <article className="staff-card" key={member.id}>
          <button type="button" className="staff-open" onClick={() => setSelected(member)}>
            <div className="staff-cover">
              {member.cover && !member.blocked && !failedImages.has(member.cover)
                ? <img loading="lazy" src={mediaUrl(`${API_BASE_URL}/staff-photos/${member.cover}/image?thumb=1`)}
                  alt={`工号 ${member.code}`} onError={() => failImage(member.cover)} />
                : <span>{member.blocked ? '暂不展示' : member.cover ? '照片暂不可用' : '照片待更新'}</span>}
              <span className={`staff-attendance-overlay${(!admin && subscription.states?.[member.id]
                ? subscription.states[member.id].published_at : member.duty_label) ? ' is-on-duty' : ''}`}>
                {dutyLabel(member)}
              </span>
            </div>
            <div className="staff-card-info">
              <strong>工号 {member.code}</strong>
              <small>{member.photo_count}张照片</small>
            </div>
          </button>
          {!admin && <SubscriptionControl id={member.id} onDuty={Boolean(member.duty_label)} subscription={subscription} />}
          {onSelect && <button type="button" className="staff-replace" disabled={busy || member.blocked}
            onClick={() => onSelect(member)}>选择此技师</button>}
          {admin && !onSelect && <button type="button" className="staff-replace" disabled={busy}
            onClick={() => setReplacement(member)}>此工号已换人</button>}
        </article>)}
      </div>
    </>}
    {selected && <MusicDialog className={admin ? 'account-sheet staff-sheet staff-admin-detail' : ordinaryMassage ? 'staff-sheet' : 'account-sheet staff-sheet'}
      title={ordinaryMassage ? `工号 ${selected.code}` : `${venue.name} · 工号 ${selected.code}`}
      scrollBody onClose={closeDetail}
      headerContent={admin ? <div className="staff-admin-header-actions">
        <button type="button" className="staff-admin-delete" disabled={busy || !detail || detailLoading}
          onClick={deleteArchive}>删除档案</button>
      </div> : undefined}
      footer={admin ? <div className="staff-admin-footer">
        <button type="button" disabled={busy || !changed} onClick={() => setDescription(detail.description)}>取消修改</button>
        <button type="button" disabled={busy || !changed || description.length > 2000}
          onClick={saveDescription}>{busy ? '处理中…' : '确认并提交'}</button>
      </div> : <div className="staff-customer-actions">
        <div className="staff-customer-buttons">
          <SubscriptionControl id={selected.id} onDuty={Boolean(selected.duty_label)} subscription={subscription} />
          {(!ordinaryMassage || customerDuty?.published_at || (!customerDuty && selected.duty_label)) &&
            <button type="button" className="staff-customer-primary" onClick={() => openTelegramLink(ordinaryMassage ? contactUrl() : bookingUrl({
              title: `你好，我想咨询${venue.name} · 工号${selected.code}`,
              details: [`人员档案编号：${selected.id}`],
              links: photo ? [new URL(mediaUrl(`${API_BASE_URL}/staff-photos/${photo.id}/image`), window.location.origin).href] : [],
            }))}>
              联系{contactName()}{ordinaryMassage ? ' · 正规按摩咨询' : '咨询'}
            </button>}
        </div>
        <SubscriptionFeedback subscription={subscription} />
        <p className="staff-meta">{subscription.authenticated
          ? '持续订阅，白班、晚班每班提醒一次，可随时取消。请先在机器人聊天中点击开始。'
          : '请从 Telegram 主入口打开，才能订阅。'}只提醒订阅后的新发布，不补发历史；出勤发布不代表实时在岗。</p>
      </div>}>
      {ordinaryMassage && !admin ? <><p className="staff-detail-duty">{dutyLabel(selected)}</p>
      {customerDuty?.published_at && <p className="staff-meta">发布时间：{customerDuty.published_at}</p>}
      <p className="staff-meta">仅提供正规按摩信息。未发布不代表实际休息或离职，已发布不代表实时在岗。</p></> : <>
      <p className="staff-detail-duty">{dutyLabel(selected)}</p>
      <p>{selected.price_usd === null || selected.price_usd === undefined
        ? '价格待确认' : `$${money(selected.price_usd)} 起 · ${selected.price_detail}`}</p>
      {selected.last_duty_at && <p className="staff-meta">最近出勤发布：{selected.last_duty_at}</p>}
      {!selected.duty_label && <p className="staff-meta">天数按最近出勤发布记录计算，未发布不代表实际休息或离职。</p>}
      <p className="staff-meta">档案编号 {selected.id} · 未确认当班出勤不代表休息或离职</p>
      {selected.duty_source === 'photo' && <p className="staff-meta">出勤标签依据会所本班次发布的出勤照片，实际在岗请联系核实。</p>}
      </>}
      {detailLoading && <p role="status">正在读取相册…</p>}
      {detailError && <p className="membership-error" role="alert">{detailError}</p>}
      {detail && !detail.photos.length && <p>照片待更新</p>}
      {photo && <>
        {failedImages.has(photo.id) ? <p role="alert">照片暂不可用，请稍后重试或联系{contactName()}核对。</p>
          : <img className="staff-detail-photo" src={mediaUrl(`${API_BASE_URL}/staff-photos/${photo.id}/image`)}
            alt={`工号 ${selected.code} 的第 ${imageIndex + 1} 张照片`} onError={() => failImage(photo.id)} />}
        <p className="staff-meta">首次收录：{photo.first_seen}</p>
        {detail.photos.length > 1 && <div className="staff-pages">
          <button type="button" disabled={imageIndex === 0} onClick={() => setImageIndex(value => value - 1)}>上一张</button>
          <span>{imageIndex + 1} / {detail.photos.length}</span>
          <button type="button" disabled={imageIndex + 1 === detail.photos.length} onClick={() => setImageIndex(value => value + 1)}>下一张</button>
        </div>}
      </>}
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
