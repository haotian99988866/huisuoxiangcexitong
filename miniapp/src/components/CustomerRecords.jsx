import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { newMemberOrderId } from '../utils/membership'
import { money } from '../utils/commerce'
import StaffGallery from './StaffGallery'
import './CustomerRecords.css'

export const consumptionDate = value => value
  ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Phnom_Penh', hour12: false }) : '暂无'
const inputDate = value => new Date(new Date(value).getTime() + 7 * 3600000).toISOString().slice(0, 16)
const identity = row => row.alias || row.display_name || (row.username ? `@${row.username}` : `ID ${row.user_id}`)
const actions = { admin_customer_save: '保存客户档案', admin_consumption_save: '登记 / 修改消费', admin_consumption_void: '作废消费' }
const contactStates = { pending: '等待发送 / 重试中', sent: '已发送', failed: '发送失败，请人工核实', cancelled: '接收权限已撤销，未发送' }

export function ConsumptionSummary({ summary }) {
  return <div className="crm-summary">
    {[['有效消费', `${summary.visits}笔`], ['实付合计', `$${money(summary.paid)}`],
      ['已退款', `$${money(summary.refunded)}`], ['净累计消费', `$${money(summary.net)}`]].map(([label, value]) =>
      <div key={label}><small>{label}</small><strong>{value}</strong></div>)}
  </div>
}

export function ConsumptionPager({ page, total, onChange, busy }) {
  return <div className="crm-pager">
    <button type="button" disabled={busy || page <= 1} onClick={() => onChange(page - 1)}>上一页</button>
    <span>{page} / {Math.max(1, Math.ceil(total / 20))} · {total}条</span>
    <button type="button" disabled={busy || page * 20 >= total} onClick={() => onChange(page + 1)}>下一页</button>
  </div>
}

export function ConsumptionRows({ records, onEdit, onVoid, busy }) {
  return <div className="crm-table"><table>
    <caption>消费明细 · 时间为柬埔寨时间</caption>
    <thead><tr><th>时间 / 会所</th><th>项目 / 技师</th><th>金额 USD</th><th>状态</th>{onEdit && <th>操作</th>}</tr></thead>
    <tbody>{records.map(row => <tr key={row.id}>
      <td>{consumptionDate(row.occurred_at)}<small>{row.venue_name}</small></td>
      <td>{row.service}<small>{row.staff_code ? `工号 ${row.staff_code}` : '未关联技师'}</small>
        {onEdit && row.note && <details><summary>内部备注</summary><p>{row.note}</p></details>}</td>
      <td>${money(row.amount)}<small>已退 ${money(row.refunded)}</small></td>
      <td>{row.status === 'void' ? '已作废' : Number(row.refunded) === Number(row.amount) ? '已全额退款' : Number(row.refunded) > 0 ? '部分退款' : '有效'}</td>
      {onEdit && <td>{row.status === 'valid' && <>
        <button type="button" disabled={busy} onClick={() => onEdit(row)}>修改 / 登记退款</button>
        <button type="button" disabled={busy} onClick={() => onVoid(row)}>作废</button>
      </>}</td>}
    </tr>)}</tbody>
  </table>{!records.length && <p className="admin-hint">暂无消费记录，不会将浏览或预约计为消费。</p>}</div>
}

function CustomerRecordDetail({ userId, onClose }) {
  const pendingKey = `crm-pending-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'telegram'}-${userId}`
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [staffPage, setStaffPage] = useState(1)
  const [showStaffPicker, setShowStaffPicker] = useState(false)
  const [reload, setReload] = useState(0)
  const [editor, setEditor] = useState(null)
  const [confirmation, setConfirmation] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const writing = useRef(false)
  const mounted = useRef(true)
  const formRef = useRef(null)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    if (editor) formRef.current?.scrollIntoView({ block: 'start' })
  }, [Boolean(editor)])
  useEffect(() => {
    const pending = sessionStorage.getItem(pendingKey)
    if (!pending) return
    try {
      const payload = JSON.parse(pending)
      if (payload.user_id !== userId || !/^[0-9a-f]{32}$/.test(payload.request_id)) throw new Error('暂存操作编号不匹配')
      setConfirmation(payload)
      setEditor({ ...payload, occurred_at: payload.occurred_at ? inputDate(payload.occurred_at) : undefined })
      setUncertain(true)
      setNotice('已恢复上次未确认操作，请刷新核对或使用原编号重试，勿重复录入。')
    } catch (failure) {
      console.error('[Restore customer operation]', failure)
      setError('暂存操作损坏，请先核对最近操作记录；不要重复登记。')
    }
  }, [pendingKey, userId])
  useEffect(() => {
    let active = true
    setBusy(true)
    setError('')
    setData(null)
    requestAdmin('admin_customer_detail', { user_id: userId, page, staff_page: staffPage })
      .then(result => {
        if (!active) return
        setData(result)
        const pending = sessionStorage.getItem(pendingKey)
        if (pending && result.audit.some(row => row.request_id === JSON.parse(pending).request_id)) {
          sessionStorage.removeItem(pendingKey)
          setConfirmation(null)
          setEditor(null)
          setUncertain(false)
          setNotice('已核对：上次操作已成功保存，未重复登记。')
        }
      })
      .catch(failure => { console.error('[Customer records]', failure); if (active) setError(failure.message) })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [userId, page, staffPage, reload])
  const editProfile = () => {
    setError('')
    setNotice('')
    setEditor({ action: 'admin_customer_save', ...data.customer, reason: '' })
  }
  const editRecord = row => {
    setShowStaffPicker(false)
    setError('')
    setNotice('')
    setEditor({
      action: 'admin_consumption_save', revision: row?.revision || 0, record_id: row?.id,
      venue_id: row?.venue_id || data.venues[0]?.id || '', staff_id: row?.staff_id || '',
      service: row?.service || '', occurred_at: inputDate(row?.occurred_at || new Date()),
      amount: row?.amount || '', refunded: row?.refunded || '0.00', note: row?.note || '', reason: '',
    })
  }
  const field = (key, value) => setEditor(current => ({
    ...current, [key]: value, ...(key === 'venue_id' ? { staff_id: '' } : {}),
  }))
  const prepare = event => {
    event.preventDefault()
    setError('')
    try {
      const payload = {
        ...editor, user_id: userId, request_id: newMemberOrderId(), confirmed: true,
      }
      if (editor.action === 'admin_consumption_save') {
        if (!editor.venue_id || !editor.service.trim() || !editor.amount) throw new Error('请填写会所、项目和实际付款金额')
        payload.venue_id = Number(editor.venue_id)
        payload.staff_id = editor.staff_id ? Number(editor.staff_id) : null
        payload.occurred_at = new Date(`${editor.occurred_at}:00+07:00`).toISOString()
      }
      if (editor.revision && !editor.reason.trim()) throw new Error('修改或作废必须填写原因')
      setConfirmation(payload)
      setUncertain(false)
    } catch (failure) { setError(failure.message) }
  }
  const save = async () => {
    if (writing.current || !confirmation) return
    writing.current = true
    setBusy(true)
    setError('')
    try {
      sessionStorage.setItem(pendingKey, JSON.stringify(confirmation))
      await requestAdmin(confirmation.action, confirmation)
      sessionStorage.removeItem(pendingKey)
      if (!mounted.current) return
      setConfirmation(null)
      setEditor(null)
      setNotice('已保存并记录操作人。未执行钱包扣款或退款。')
      setUncertain(false)
      setReload(value => value + 1)
    } catch (failure) {
      console.error('[Save customer record]', failure)
      if (!mounted.current) return
      setError(failure.message)
      if ([400, 401, 403, 409].includes(failure.status)) {
        if ([400, 409].includes(failure.status)) sessionStorage.removeItem(pendingKey)
        setConfirmation(null)
        if ([401, 403].includes(failure.status)) { setData(null); setEditor(null) }
      } else {
        setUncertain(true)
        setNotice('保存结果未确认。请保留本次操作编号，刷新核对后可原样重试；勿重新建立同一笔消费。')
      }
    } finally {
      writing.current = false
      if (mounted.current) setBusy(false)
    }
  }
  return <section className="crm-detail">
    <div className="admin-header"><button type="button" disabled={busy || !!confirmation} onClick={onClose}>← 客户列表</button>
      <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>刷新记录</button></div>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {notice && <p role="status" className="admin-notice">{notice}</p>}
    {busy && <p role="status">正在处理…</p>}
    {data && <>
      <section className="admin-card">
        <h2>{identity(data.customer)}</h2>
        <p>{data.customer.username ? `@${data.customer.username}` : '无用户名记录'} · ID {userId}</p>
        <p className="admin-hint">Telegram 名称：{data.customer.display_name || '暂无记录'}</p>
        <ConsumptionSummary summary={data.summary} />
        <p className="admin-hint">最近消费：{consumptionDate(data.summary.last_visit)} · 净累计排除作废并减去已退款</p>
        <dl className="crm-preferences">
          <dt>服务偏好</dt><dd>{data.customer.preferences || '未登记'}</dd>
          <dt>避免安排</dt><dd>{data.customer.avoidances || '未登记'}</dd>
          <dt>内部备注</dt><dd>{data.customer.notes || '未登记'}</dd>
        </dl>
        <p className="admin-hint">以上仅管理员可见。请只记录必要服务信息，勿记录敏感私人信息。</p>
        <button type="button" disabled={busy || !!editor} onClick={editProfile}>{data.customer.revision ? '编辑客户档案' : '建立客户档案'}</button>
      </section>
      <section className="admin-card">
        <div className="admin-header"><h2>曾消费的技师 · {data.staff_total}位</h2>
          <button type="button" disabled={busy || !!editor || !data.customer.revision} onClick={() => editRecord()}>
            选择技师并登记消费
          </button>
        </div>
        <p className="admin-hint">由已核实、未作废的消费自动关联，不代表固定或偏好技师。退款保留消费事实并扣减金额；作废或改选技师后自动更新。</p>
        <div className="crm-table"><table>
          <thead><tr><th>会所 / 技师</th><th>次数 / 净累计</th><th>最近消费</th></tr></thead>
          <tbody>{data.staff_history.map(row => <tr key={row.staff_id}>
            <td>{row.venue_name}<small>工号 {row.staff_code} · 档案{row.staff_id}{row.ended_at ? '（历史人员）' : ''}</small></td>
            <td>{row.visits}次<small>${money(row.net)} · 已退 ${money(row.refunded)}</small></td>
            <td>{consumptionDate(row.last_visit)}</td>
          </tr>)}</tbody>
        </table>{!data.staff_history.length && <p>暂无已关联技师。登记消费时选择人员档案；旧消费可通过“修改”补充技师。</p>}</div>
        <ConsumptionPager page={staffPage} total={data.staff_total} onChange={setStaffPage} busy={busy || !!editor} />
      </section>
      <section className="admin-card">
        <div className="admin-header"><h2>消费登记</h2>
          <button type="button" disabled={busy || !!editor || !data.customer.revision} onClick={() => editRecord()}>登记消费</button></div>
        <p className="admin-hint">只录入已完成的真实消费；金额由你核实，不从起价或浏览推算。客户可查看时间、项目、技师、实付及退款。</p>
        <ConsumptionRows records={data.records} onEdit={editRecord} busy={busy || !!editor}
          onVoid={row => setEditor({ action: 'admin_consumption_void', record_id: row.id, revision: row.revision, reason: '' })} />
        <ConsumptionPager page={page} total={data.total} onChange={setPage} busy={busy || !!editor} />
      </section>
      <section className="admin-card"><h2>联系提醒</h2>
        <p className="admin-hint">点击 App 联系入口时发送内部客户卡，5分钟内去重；不代表客户已发消息。</p>
        {data.contacts.map((row, index) => <p key={index}>{consumptionDate(row.created_at)} · {contactStates[row.status]} · 尝试{row.attempts}次</p>)}
        {!data.contacts.length && <p>暂无联系提醒记录</p>}
      </section>
      <details className="admin-card"><summary>最近30次登记操作</summary>
        {data.audit.map(row => <p key={row.request_id}>{consumptionDate(row.created_at)} · 管理员{row.actor_id} · {actions[row.action]}
          {row.reason && <small className="crm-audit-reason">{row.reason}</small>}</p>)}
      </details>
    </>}
    {editor && <form ref={formRef} className="admin-card crm-form" onSubmit={prepare}>
      <h2>{actions[editor.action]}</h2>
      <fieldset disabled={busy || !!confirmation}>
        {editor.action === 'admin_customer_save' ? <>
          <label>客户称呼<input maxLength={128} value={editor.alias} onChange={e => field('alias', e.target.value)} /></label>
          {[['preferences', '服务偏好'], ['avoidances', '避免安排'], ['notes', '内部备注']].map(([key, label]) =>
            <label key={key}>{label}<textarea maxLength={2000} value={editor[key]} onChange={e => field(key, e.target.value)} /></label>)}
        </> : editor.action === 'admin_consumption_save' ? <>
          <label>会所<select required value={editor.venue_id} onChange={e => field('venue_id', e.target.value)}>
            <option value="">请选择</option>{data?.venues.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select></label>
          <label>服务技师<select value={editor.staff_id} onChange={e => field('staff_id', e.target.value)}>
            <option value="">暂不关联，后续可补录</option>
            {data?.staff.filter(row => row.venue_id === Number(editor.venue_id)).map(row =>
              <option key={row.id} value={row.id}>工号 {row.code} · 档案{row.id}{row.ended_at ? '（历史人员）' : ''}</option>)}
          </select></label>
          <button type="button" disabled={!editor.venue_id} onClick={() => setShowStaffPicker(value => !value)}>
            {showStaffPicker ? '收起人员照片' : '从人员档案照片选择技师'}
          </button>
          {showStaffPicker && data?.venues.find(row => row.id === Number(editor.venue_id)) && <StaffGallery
            key={editor.venue_id} venue={data.venues.find(row => row.id === Number(editor.venue_id))}
            admin onSelect={member => { field('staff_id', member.id); setShowStaffPicker(false) }} />}
          {!!editor.staff_id && <p className="admin-hint">已选择工号 {data?.staff.find(row => row.id === Number(editor.staff_id))?.code}
            {' '}· 档案{editor.staff_id}；确认保存真实消费后才与客户关联。</p>}
          <label>消费时间（柬埔寨时间）<input required type="datetime-local" value={editor.occurred_at} onChange={e => field('occurred_at', e.target.value)} /></label>
          <label>消费项目<input required maxLength={128} value={editor.service} onChange={e => field('service', e.target.value)} /></label>
          <label>实际付款 USD<input required inputMode="decimal" pattern="\d{1,10}(\.\d{1,2})?" value={editor.amount} onChange={e => field('amount', e.target.value)} /></label>
          <label>已经实际退款 USD<input required inputMode="decimal" pattern="\d{1,10}(\.\d{1,2})?" value={editor.refunded} onChange={e => field('refunded', e.target.value)} /></label>
          <label>本次消费内部备注<textarea maxLength={2000} value={editor.note} onChange={e => field('note', e.target.value)} /></label>
          <p className="admin-hint">这里记录线下实付及已退款事实，不执行任何资金操作。</p>
        </> : <p>作废后不计入累计消费，客户仍可看到作废状态，原记录不会删除。</p>}
        {!!editor.revision && <label>修改 / 作废原因<textarea required maxLength={500} value={editor.reason} onChange={e => field('reason', e.target.value)} /></label>}
        <div className="crm-pager"><button type="button" onClick={() => setEditor(null)}>取消</button><button type="submit">核对登记</button></div>
      </fieldset>
      {confirmation && <section className="crm-confirm" aria-label="确认登记">
        <h3>确认保存到客户 ID {userId}</h3>
        {confirmation.action === 'admin_consumption_save' && <p>
          {consumptionDate(confirmation.occurred_at)} · {confirmation.service}<br />
          {data?.venues.find(row => row.id === Number(confirmation.venue_id))?.name}
          {' · '}{confirmation.staff_id
            ? `工号 ${data?.staff.find(row => row.id === Number(confirmation.staff_id))?.code} · 档案${confirmation.staff_id}` : '未关联技师'}<br />
          实付 ${money(confirmation.amount)} · 已退 ${money(confirmation.refunded)}
        </p>}
        <p>操作编号：<small>{confirmation.request_id}</small></p>
        <button type="button" disabled={busy || uncertain} onClick={() => setConfirmation(null)}>返回修改</button>
        <button type="button" disabled={busy} onClick={save}>确认保存</button>
      </section>}
    </form>}
  </section>
}

export default function CustomerRecords({ initialUserId }) {
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [reload, setReload] = useState(0)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [userId, setUserId] = useState(initialUserId || null)
  useEffect(() => {
    if (userId) return
    let active = true
    setBusy(true)
    setData(null)
    setError('')
    requestAdmin('admin_customers', { query: search, page })
      .then(result => { if (active) setData(result) })
      .catch(failure => { console.error('[Customer directory]', failure); if (active) setError(failure.message) })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [search, page, reload, userId])
  if (userId) return <CustomerRecordDetail key={userId} userId={userId} onClose={() => setUserId(null)} />
  return <section className="admin-card">
    <div className="admin-header"><h2>客户档案与消费</h2><button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>刷新</button></div>
    <p className="admin-hint">按 Telegram ID、用户名、名称或登记称呼搜索。未找到客户时，请让客户先启动机器人或打开 App，以确认账号归属。</p>
    <form className="crm-search" onSubmit={e => { e.preventDefault(); setPage(1); setSearch(query.trim()); setReload(value => value + 1) }}>
      <input aria-label="搜索客户" maxLength={128} value={query} onChange={e => setQuery(e.target.value)} placeholder="ID / @用户名 / 客户称呼" />
      <button disabled={busy} type="submit">搜索</button>
    </form>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {busy && <p role="status">正在读取…</p>}
    {data && <>
      <div className="crm-table"><table><thead><tr><th>客户</th><th>消费</th><th>档案</th></tr></thead>
        <tbody>{data.customers.map(row => <tr key={row.user_id}>
          <td><button type="button" onClick={() => setUserId(row.user_id)}>{identity(row)}</button>
            <small>{row.username ? `@${row.username}` : '暂无用户名'} · ID {row.user_id}</small></td>
          <td>{row.visits}笔<small>净累计 ${money(row.net)}</small></td>
          <td>{row.registered ? '已建档' : '待建档'}</td>
        </tr>)}</tbody>
      </table>{!data.customers.length && <p>未找到已验证客户</p>}</div>
      <ConsumptionPager page={page} total={data.total} onChange={setPage} busy={busy} />
    </>}
  </section>
}
