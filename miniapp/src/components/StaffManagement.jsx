import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { newMemberOrderId } from '../utils/membership'
import { API_BASE_URL } from '../utils/twa'
import { mediaUrl } from '../utils/tenant'
import MusicDialog from './MusicDialog'
import './StaffGallery.css'
import './StaffManagement.css'

const statuses = { pending: '待发送', sending: '发送结果待核实', unconfirmed: '发送结果未确认，请人工核实', sent: '已发送', cancelled: '已取消' }
const date = value => value ? new Date(value.replace(' ', 'T')).toLocaleString('zh-CN', { hour12: false }) : '尚未设置'

export default function StaffManagement({ venue }) {
  const [reload, setReload] = useState(0)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [editor, setEditor] = useState(null)
  const [pending, setPending] = useState(null)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [notice, setNotice] = useState('')
  const [failedImages, setFailedImages] = useState(new Set())
  const writeLock = useRef(false)
  const form = useRef(null)
  const key = `staff-management-pending-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'telegram'}-${venue.id}`
  useEffect(() => {
    const saved = sessionStorage.getItem(key)
    if (!saved) return
    try {
      const payload = JSON.parse(saved)
      if (!/^[0-9a-f]{32}$/.test(payload.request_id) || !Number.isSafeInteger(payload.staff_id)) throw new Error('暂存操作无效')
      setPending(payload); setEditor({ ...payload, id: payload.staff_id, code: payload.staff_code })
      setUncertain(true)
    } catch (failure) {
      console.error('[Restore staff management]', failure)
      setUncertain(true)
      setError('暂存操作损坏，请先人工核对标签和订阅，不要重复提交。')
    }
  }, [key])
  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    requestAdmin('admin_staff_management', { venue_id: venue.id, mode: 'all' })
      .then(result => { if (active) setData(result) })
      .catch(failure => {
        console.error('[Staff management]', failure)
        if (active) { setData(null); setError(failure.message) }
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [venue.id, reload])
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && !editor && !busy) setReload(value => value + 1)
    }, 60000)
    return () => clearInterval(timer)
  }, [editor, busy])
  const open = (member, subscribe = false) => {
    if (uncertain) return
    setError(''); setNotice(''); setPending(null); setUncertain(false)
    setEditor({ ...member, subscribed: subscribe ? !member.subscribed : member.subscribed, authorized: false })
  }
  const close = () => {
    if (writeLock.current) return
    if (uncertain) { setError('保存结果尚未确认，请用原操作编号重试，不能丢弃后重复登记。'); return }
    if (pending) { setPending(null); return }
    setEditor(null)
  }
  const prepare = event => {
    event.preventDefault()
    setPending({
      staff_id: editor.id, staff_code: editor.code, revision: editor.revision,
      employment: editor.employment, circumstance: editor.circumstance,
      customer_authorized: editor.customer_authorized,
      subscribed: editor.subscribed, authorized: editor.authorized, confirmed: true,
      request_id: newMemberOrderId(),
    })
  }
  const save = async () => {
    if (writeLock.current || !pending) return
    writeLock.current = true; setBusy(true); setError('')
    try {
      sessionStorage.setItem(key, JSON.stringify(pending))
      await requestAdmin('admin_staff_management_save', pending)
      sessionStorage.removeItem(key); setPending(null); setEditor(null); setUncertain(false)
      setNotice('已保存管理标签和当前管理员的订阅设置。'); setReload(value => value + 1)
    } catch (failure) {
      console.error('[Save staff management]', failure); setError(failure.message)
      if ([400, 401, 403, 409].includes(failure.status)) {
        sessionStorage.removeItem(key); setPending(null); setUncertain(false)
        if (failure.status === 409) { setEditor(null); setReload(value => value + 1) }
        if ([401, 403].includes(failure.status)) { setData(null); setEditor(null) }
      } else setUncertain(true)
    } finally { writeLock.current = false; setBusy(false) }
  }
  return <section className="staff-gallery staff-management" aria-label="内部人员管理">
    <div className="staff-heading"><h2>人员概览</h2><button type="button" disabled={busy || !!editor || loading}
      onClick={() => setReload(value => value + 1)}>刷新</button></div>
    <p className="staff-meta">仅管理员可见。标签为人工管理记录；未发布出勤不代表实际缺勤。</p>
    {!editor && error && <p className="membership-error" role="alert">{error}</p>}
    {notice && <p className="membership-notice" role="status">{notice}</p>}
    {loading && <p role="status">正在读取人员…</p>}
    {data && <>
      <p className="staff-meta">全部 {data.total} 位 · {data.shift} · 订阅只提醒启用后的新出勤发布</p>
      <div className="staff-grid">{data.members.map(member => <article key={member.id} className="staff-card">
        <button className="staff-open" type="button" disabled={busy || !!editor || uncertain} onClick={() => open(member)}>
          <div className="staff-cover">
            {member.cover && !member.blocked && !failedImages.has(member.cover) ?
              <img alt={`工号 ${member.code}`} loading="lazy" src={mediaUrl(`${API_BASE_URL}/staff-photos/${member.cover}/image?thumb=1`)}
                onError={() => setFailedImages(current => new Set([...current, member.cover]))} /> :
              <span>{member.blocked ? '暂不展示' : member.cover ? '照片暂不可用' : '照片待更新'}</span>}
            <span className={`staff-attendance-overlay${member.duty_label ? ' is-on-duty' : ''}`}>{member.attendance_overlay}</span>
          </div>
          <div className="staff-card-info"><strong>工号 {member.code}</strong>
            <div className="staff-management-tags">{[member.employment, member.circumstance].filter(Boolean).map(tag => <span key={tag}>{tag}</span>)}</div>
          </div>
        </button>
        <button className="staff-replace" type="button" disabled={busy || !!editor || uncertain || member.employment === '离职' || member.blocked}
          onClick={() => open(member, true)}>{member.subscribed ? '已订阅 · 取消' : '订阅出勤'}</button>
        {member.notification && <small className="staff-delivery-status">{statuses[member.notification.status]}</small>}
      </article>)}</div>
      {!data.members.length && <p className="staff-meta">暂无人员档案。</p>}
    </>}
    {editor && <MusicDialog title={`工号 ${editor.code}`} scrollBody onClose={close}
      footer={<div className="staff-management-actions">
        <button type="button" disabled={busy || uncertain} onClick={close}>{pending ? '返回修改' : '取消'}</button>
        <button className="staff-management-primary" type="button" disabled={busy || loading}
          onClick={() => pending ? save() : form.current?.requestSubmit()}>
          {busy ? '保存中…' : pending ? uncertain ? '原编号重试' : '确认保存' : '下一步：核对'}</button>
      </div>}>
      {error && <p className="membership-error" role="alert">{error}</p>}
      {uncertain && <p className="membership-notice">保存结果未确认，请保留原编号重试。不会自动重复通知。</p>}
      {!pending ? <form ref={form} className="staff-management-form" onSubmit={prepare}>
        <p className="staff-meta">标签更新时间：{date(editor.updated_at)}</p>
        <fieldset disabled={busy}>
          <label>人员状态<select value={editor.employment} onChange={e => setEditor(current => ({
            ...current, employment: e.target.value, ...(e.target.value === '离职' ? { subscribed: false, customer_authorized: false } : {}),
          }))}><option value="">未设置</option><option>在职</option><option>离职</option></select></label>
          <label>临时管理标签<select value={editor.circumstance} onChange={e => setEditor(current => ({ ...current, circumstance: e.target.value }))}>
            <option value="">无 / 清除</option><option>回国</option><option>请假</option>
          </select></label>
          {venue.id === 1 && <><label className="staff-management-check"><input type="checkbox" checked={Boolean(editor.customer_authorized)}
            disabled={editor.employment === '离职' || editor.blocked}
            onChange={e => setEditor(current => ({ ...current, customer_authorized: e.target.checked }))} />
            已获人员授权，向客人开放正规按摩出勤订阅（不含成人服务）</label>
          <p className="staff-meta">关闭客人授权会停止此人员全部客人订阅；客人自行订阅，仅收到工号和出勤发布时间。</p></>}
          <label className="staff-management-check"><input type="checkbox" checked={editor.subscribed}
            disabled={editor.employment === '离职' || editor.blocked}
            onChange={e => setEditor(current => ({ ...current, subscribed: e.target.checked }))} />订阅个人出勤（仅当前管理员）</label>
          {editor.subscribed && !data?.members.find(row => row.id === editor.id)?.subscribed &&
            <label className="staff-management-check"><input type="checkbox" required checked={editor.authorized}
              onChange={e => setEditor(current => ({ ...current, authorized: e.target.checked }))} />已取得人员授权，仅用于内部出勤管理</label>}
          <p className="staff-meta">机器人私信只包含会所、工号、班次和发布时间，不发送照片或管理标签。离职会停止所有管理员的订阅；工号换人不转移订阅。</p>
        </fieldset>
      </form> : <section className="staff-management-confirm">
        <h3>核对管理设置</h3>
        <p>人员状态：{pending.employment || '未设置'}</p>
        <p>临时标签：{pending.circumstance || '无 / 清除'}</p>
        <p>当前管理员订阅：{pending.subscribed ? '开启' : '关闭'}</p>
        {venue.id === 1 && <p>正规按摩客人订阅授权：{pending.customer_authorized ? '开放' : '关闭，停止已有客人订阅'}</p>}
        {pending.employment === '离职' && <p>确认后停止该人员所有管理员的订阅。</p>}
        <p className="staff-meta">同一人员同一班次只提醒一次。发送结果不明时不自动重发，请人工核实。</p>
      </section>}
    </MusicDialog>}
  </section>
}
