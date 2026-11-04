import { useEffect, useState } from 'react'
import MusicDialog from './MusicDialog'
import { API_BASE_URL, openTelegramLink } from '../utils/twa'
import { mediaUrl, tenantHeaders, contactName } from '../utils/tenant'
import { requestAdmin } from '../utils/admin'
import { bookingUrl } from '../utils/booking'
import { useAnalyticsScreen } from '../utils/analytics'
import { money } from '../utils/commerce'
import StaffCustomerActions from './StaffCustomerActions'
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
  const [customerDuty, setCustomerDuty] = useState(null)
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
    setCustomerDuty(null)
    setDetailError('')
    setImageIndex(0)
    setFailedImages(new Set())
    if (!selected) return
    let active = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    setDetailLoading(true)
    requestStaff('get_staff_detail', { staff_id: selected.id }, controller.signal)
      .then(result => { if (active) setDetail(result) })
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
  }, [selected])

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
              <span className={`staff-attendance-overlay${member.duty_label ? ' is-on-duty' : ''}`}>
                {member.attendance_overlay || member.duty_label || '出勤记录待确认'}
              </span>
            </div>
            <div className="staff-card-info">
              <strong>工号 {member.code}</strong>
              <small>{member.photo_count}张照片</small>
            </div>
          </button>
          {onSelect && <button type="button" className="staff-replace" disabled={busy || member.blocked}
            onClick={() => onSelect(member)}>选择此技师</button>}
          {admin && !onSelect && <button type="button" className="staff-replace" disabled={busy}
            onClick={() => setReplacement(member)}>此工号已换人</button>}
        </article>)}
      </div>
    </>}
    {selected && <MusicDialog className={ordinaryMassage ? 'staff-sheet' : 'account-sheet staff-sheet'}
      title={ordinaryMassage ? `工号 ${selected.code}` : `${venue.name} · 工号 ${selected.code}`}
      scrollBody={ordinaryMassage} onClose={() => setSelected(null)}
      footer={ordinaryMassage ? <StaffCustomerActions key={selected.id} staffId={selected.id} onState={setCustomerDuty} /> : undefined}>
      {ordinaryMassage ? <><p className="staff-detail-duty">{customerDuty ? customerDuty.published_at
        ? `${customerDuty.shift}出勤已发布` : '本班次尚未发布出勤' : '正在核对本班次出勤…'}</p>
      {customerDuty?.published_at && <p className="staff-meta">发布时间：{customerDuty.published_at}</p>}
      <p className="staff-meta">仅提供正规按摩信息。未发布不代表实际休息或离职，已发布不代表实时在岗。</p></> : <>
      <p className="staff-detail-duty">{selected.attendance_overlay || selected.duty_label || '出勤记录待确认'}</p>
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
      {!ordinaryMassage && <button type="button" onClick={() => openTelegramLink(bookingUrl({
        title: `你好，我想咨询${venue.name} · 工号${selected.code}`,
        details: [`人员档案编号：${selected.id}`],
        links: photo ? [new URL(mediaUrl(`${API_BASE_URL}/staff-photos/${photo.id}/image`), window.location.origin).href] : [],
      }))}>联系{contactName()}咨询</button>}
    </MusicDialog>}
    {replacement && <MusicDialog className="account-sheet" title="确认工号换人" onClose={() => { if (!busy) setReplacement(null) }}>
      <p>{venue.name} · 工号 {replacement.code} · 原档案 {replacement.id}</p>
      <p>仅在工号实际换成另一个人时操作，不是更新照片。旧档案及相册保留但退出公开展示；新人使用新的独立编号。</p>
      <p>请先确认，再发布新人的出勤及照片；系统不会将已经归档的旧照片自动转移给新人。</p>
      <button type="button" disabled={busy} onClick={changePerson}>{busy ? '保存中…' : '确认换人，建立新人档案'}</button>
    </MusicDialog>}
  </section>
}
