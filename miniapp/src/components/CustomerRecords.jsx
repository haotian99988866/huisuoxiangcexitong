import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { newMemberOrderId } from '../utils/membership'
import { money } from '../utils/commerce'
import { hasTelegramBackButton, registerBackOverlay } from '../utils/twa'
import StaffGallery from './StaffGallery'
import MusicDialog from './MusicDialog'
import MusicIcon from './MusicIcon'
import './CustomerRecords.css'

export const consumptionDate = value => value
  ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Phnom_Penh', hour12: false }) : '暂无'
const inputDate = value => new Date(new Date(value).getTime() + 7 * 3600000).toISOString().slice(0, 16)
const identity = row => row.alias || row.display_name || (row.username ? `@${row.username}` : `ID ${row.user_id}`)
const actions = { admin_customer_save: '保存客户档案', admin_customer_bind: '绑定 Telegram 账号', admin_consumption_save: '登记 / 修改消费', admin_consumption_void: '作废消费' }
const customerAccount = row => row.telegram_user_id ? `Telegram ID ${row.telegram_user_id}` : '未绑定 Telegram · 线下客户'
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

function CustomerRecordDetail({ userId, onClose, createOnOpen = false }) {
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
  const [tab, setTab] = useState('overview')
  const [profileExtras, setProfileExtras] = useState(false)
  const [consumptionExtras, setConsumptionExtras] = useState(false)
  const [startConsumption, setStartConsumption] = useState(false)
  const writing = useRef(false)
  const mounted = useRef(true)
  const formRef = useRef(null)
  const initialFormOpened = useRef(false)
  const returnToList = useRef(onClose)
  returnToList.current = onClose
  useEffect(() => registerBackOverlay(() => returnToList.current()), [])
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    formRef.current?.closest('.dialog-panel-body')?.scrollTo({ top: 0 })
  }, [Boolean(confirmation)])
  useEffect(() => {
    const pending = sessionStorage.getItem(pendingKey)
    if (!pending) return
    try {
      const payload = JSON.parse(pending)
      if (payload.user_id !== userId || !/^[0-9a-f]{32}$/.test(payload.request_id)) throw new Error('暂存操作编号不匹配')
      setConfirmation(payload)
      setEditor({ ...payload, occurred_at: payload.occurred_at ? inputDate(payload.occurred_at) : undefined })
      setUncertain(true)
      setProfileExtras(true)
      setConsumptionExtras(true)
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
  const editProfile = (nextConsumption = false) => {
    setError('')
    setNotice('')
    setProfileExtras(Boolean(data.customer.preferences || data.customer.avoidances || data.customer.notes))
    setStartConsumption(nextConsumption)
    setEditor({ action: 'admin_customer_save', ...data.customer, reason: '' })
  }
  const editRecord = row => {
    setShowStaffPicker(false)
    setError('')
    setNotice('')
    setConsumptionExtras(Boolean(row?.note || Number(row?.refunded)))
    setEditor({
      action: 'admin_consumption_save', revision: row?.revision || 0, record_id: row?.id,
      venue_id: row?.venue_id || '', staff_id: row?.staff_id || '',
      service: row?.service || '', occurred_at: inputDate(row?.occurred_at || new Date()),
      amount: row?.amount || '', refunded: row?.refunded || '0.00', note: row?.note || '', reason: '',
    })
  }
  useEffect(() => {
    if (data?.customer.revision && startConsumption && !editor && !busy) {
      setStartConsumption(false)
      editRecord()
    }
  }, [data, startConsumption, Boolean(editor), busy])
  const closeEditor = () => {
    if (writing.current) return
    if (uncertain) {
      setError('本次保存结果未确认，请先“刷新核对”或使用原编号重试，不能丢弃后重新登记。')
      return
    }
    if (confirmation) { setConfirmation(null); return }
    setEditor(null)
    setShowStaffPicker(false)
    setStartConsumption(false)
  }
  useEffect(() => {
    if (!createOnOpen || initialFormOpened.current || !data || busy || editor) return
    initialFormOpened.current = true
    if (!data.customer.revision) editProfile()
  }, [createOnOpen, data, busy, Boolean(editor)])
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
      if (editor.action === 'admin_customer_bind') {
        payload.telegram_user_id = Number(editor.telegram_user_id)
        if (!Number.isSafeInteger(payload.telegram_user_id) || payload.telegram_user_id <= 0) throw new Error('请填写有效 Telegram 数字 ID')
      }
      if (editor.action === 'admin_consumption_save') {
        if (!editor.venue_id || !editor.service.trim() || !editor.amount) throw new Error('请填写会所、项目和实际付款金额')
        payload.venue_id = Number(editor.venue_id)
        payload.staff_id = editor.staff_id ? Number(editor.staff_id) : null
        payload.occurred_at = new Date(`${editor.occurred_at}:00+07:00`).toISOString()
        payload.refunded = editor.refunded.trim() || '0.00'
        if (!/^\d{1,10}(?:\.\d{1,2})?$/.test(editor.amount) ||
            !/^\d{1,10}(?:\.\d{1,2})?$/.test(payload.refunded)) {
          throw new Error('金额须为美元数字，最多两位小数')
        }
        if (Number(editor.amount) <= 0 || Number(payload.refunded) > Number(editor.amount)) {
          throw new Error('实付金额必须大于0，已退款金额不能超过实付金额')
        }
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
      if (confirmation.action === 'admin_customer_bind') setTab('overview')
      else if (confirmation.action !== 'admin_customer_save') setTab('consumption')
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
    {!editor && error && <p role="alert" className="admin-error">{error}</p>}
    {!editor && notice && <p role="status" className="admin-notice">{notice}</p>}
    {!editor && busy && <p role="status">正在读取客户…</p>}
    {data && <>
      <section className="admin-card crm-profile-card">
        <div className="crm-profile-heading">
          <span className="crm-avatar" aria-hidden="true">{identity(data.customer).slice(0, 1)}</span>
          <div className="crm-profile-identity">
            <h2>{identity(data.customer)}</h2>
            <p>{data.customer.username ? `@${data.customer.username} · ` : ''}{customerAccount(data.customer)}</p>
          </div>
          {!hasTelegramBackButton && <button className="crm-return" type="button" aria-label="返回客户列表"
            title="返回客户列表" disabled={busy || !!editor} onClick={onClose}><MusicIcon name="list" /></button>}
        </div>
        <div className="crm-customer-actions">
          <button className="crm-primary" type="button" disabled={busy || !!editor}
            onClick={() => data.customer.revision ? editRecord() : editProfile(true)}>
            <MusicIcon name="plus" size={16} />登记消费</button>
          <button className="crm-secondary" type="button" disabled={busy || !!editor} onClick={() => editProfile()}>
            {data.customer.revision ? '编辑档案' : '建立档案'}
          </button>
          {!data.customer.telegram_user_id && <button className="crm-secondary" type="button" disabled={busy || !!editor}
            onClick={() => setEditor({ action: 'admin_customer_bind', revision: data.customer.revision, telegram_user_id: '', reason: '' })}>
            绑定账号
          </button>}
        </div>
      </section>
      <nav className="crm-tabs" aria-label="客户资料分类">
        {[['overview', '概览'], ['consumption', '消费'], ['staff', '技师'], ['history', '操作记录']].map(([key, label]) =>
          <button key={key} type="button" aria-pressed={tab === key} disabled={busy || !!editor} onClick={() => setTab(key)}>{label}</button>)}
      </nav>
      {tab === 'overview' && <section className="admin-card crm-overview">
        <ConsumptionSummary summary={data.summary} />
        <p className="admin-hint">最近消费：{consumptionDate(data.summary.last_visit)}</p>
        {!data.customer.revision && <p className="crm-empty">尚未建档。点击“建立档案”即可保存；偏好和备注都可稍后补充。</p>}
        <dl className="crm-preferences">
          <div><dt>服务偏好</dt><dd>{data.customer.preferences || '未登记'}</dd></div>
          <div><dt>避免安排</dt><dd>{data.customer.avoidances || '未登记'}</dd></div>
          <div><dt>内部备注</dt><dd>{data.customer.notes || '未登记'}</dd></div>
        </dl>
        <p className="admin-hint">偏好和内部备注仅管理员可见。</p>
      </section>}
      {tab === 'staff' && <section className="admin-card">
        <h2>曾消费的技师 · {data.staff_total}位</h2>
        <p className="admin-hint">来自已登记的有效消费；补录技师请到“消费”修改对应记录。</p>
        <div className="crm-table"><table>
          <thead><tr><th>会所 / 技师</th><th>次数 / 净累计</th><th>最近消费</th></tr></thead>
          <tbody>{data.staff_history.map(row => <tr key={row.staff_id}>
            <td>{row.venue_name}<small>工号 {row.staff_code} · 档案{row.staff_id}{row.ended_at ? '（历史人员）' : ''}</small></td>
            <td>{row.visits}次<small>${money(row.net)} · 已退 ${money(row.refunded)}</small></td>
            <td>{consumptionDate(row.last_visit)}</td>
          </tr>)}</tbody>
        </table>{!data.staff_history.length && <p>暂无已关联技师。登记消费时选择人员档案；旧消费可通过“修改”补充技师。</p>}</div>
        <ConsumptionPager page={staffPage} total={data.staff_total} onChange={setStaffPage} busy={busy || !!editor} />
      </section>}
      {tab === 'consumption' && <section className="admin-card">
        <h2>消费记录</h2>
        <ConsumptionRows records={data.records} onEdit={editRecord} busy={busy || !!editor}
          onVoid={row => setEditor({ action: 'admin_consumption_void', record_id: row.id, revision: row.revision, reason: '' })} />
        <ConsumptionPager page={page} total={data.total} onChange={setPage} busy={busy || !!editor} />
      </section>}
      {tab === 'history' && <>
      <section className="admin-card"><h2>联系提醒</h2>
        <p className="admin-hint">点击 App 联系入口时发送内部客户卡，5分钟内去重；不代表客户已发消息。</p>
        {data.contacts.map((row, index) => <p key={index}>{consumptionDate(row.created_at)} · {contactStates[row.status]} · 尝试{row.attempts}次</p>)}
        {!data.contacts.length && <p>暂无联系提醒记录</p>}
      </section>
      <section className="admin-card"><h2>最近30次登记操作</h2>
        {data.audit.map(row => <p key={row.request_id}>{consumptionDate(row.created_at)} · 管理员{row.actor_id} · {actions[row.action]}
          {row.reason && <small className="crm-audit-reason">{row.reason}</small>}</p>)}
        {!data.audit.length && <p>暂无登记操作</p>}
      </section>
      </>}
    </>}
    {editor && <MusicDialog className="crm-editor-sheet" title={confirmation ? '核对并确认' :
      editor.action === 'admin_customer_save' ? (editor.revision ? '编辑客户档案' : '建立客户档案') :
        editor.action === 'admin_customer_bind' ? '绑定 Telegram 账号' :
          editor.action === 'admin_consumption_void' ? '作废消费' : editor.revision ? '修改消费' : '登记消费'}
      onClose={closeEditor} scrollBody headerContent={<ol className="crm-editor-steps" aria-label="登记步骤">
        <li aria-current={!confirmation ? 'step' : undefined}><span>{confirmation ? <MusicIcon name="check" size={14} /> : '1'}</span>填写资料</li>
        <li aria-current={confirmation ? 'step' : undefined}><span>2</span>核对保存</li>
      </ol>} footer={<div className="crm-editor-actions">
        {!confirmation ? <>
          <button type="button" disabled={busy} onClick={closeEditor}>取消</button>
          <button className="crm-primary" type="button" disabled={busy || !data}
            onClick={() => formRef.current?.requestSubmit()}>下一步：核对</button>
        </> : <>
          <button type="button" disabled={busy || uncertain} onClick={() => setConfirmation(null)}>返回修改</button>
          <button className="crm-primary" type="button" disabled={busy || !data} onClick={save}>
            {busy ? '正在保存…' : uncertain ? '原编号重试' : '确认保存'}</button>
        </>}
      </div>}>
      <div className="crm-editor-customer">
        <div><strong>{identity(data?.customer || { user_id: userId })}</strong>
          <small>{data?.customer ? customerAccount(data.customer) : `档案 ${Math.abs(userId)}`}</small></div>
      </div>
      {error && <p role="alert" className="admin-error">{error}</p>}
      {notice && <p role="status" className="admin-notice">{notice}</p>}
      <form ref={formRef} className="crm-form" onSubmit={prepare}>
      {!confirmation && <fieldset disabled={busy || !data}>
        {editor.action === 'admin_customer_bind' ? <>
          <p>绑定后，该 Telegram 账号可以查看此档案的消费记录。请先核实客人身份。</p>
          <label>Telegram 数字 ID<input required inputMode="numeric" pattern="[1-9]\d{0,15}"
            value={editor.telegram_user_id} onChange={e => field('telegram_user_id', e.target.value)} /></label>
          <p className="admin-hint">请让客人先启动机器人或打开 App。账号已有档案时不会自动合并。</p>
        </> : editor.action === 'admin_customer_save' ? <>
          <section className="crm-form-group">
            <h3>基本资料</h3>
            <p className="admin-hint">{startConsumption ? '保存后进入消费登记。' : '称呼用于识别客户，服务资料可稍后补充。'}</p>
            <label>客户称呼{data.customer.user_id > 0 ? '（选填）' : ''}<input required={data.customer.user_id < 0} maxLength={128} value={editor.alias}
              placeholder={data?.customer.display_name || (data.customer.user_id > 0 ? '不填则使用 Telegram 名称' : '方便识别的客户称呼')} onChange={e => field('alias', e.target.value)} /></label>
          </section>
          <details className="crm-form-group crm-profile-extras" open={profileExtras} onToggle={event => setProfileExtras(event.currentTarget.open)}>
            <summary><span>服务资料<small>偏好、安排与内部备注 · 选填</small></span><MusicIcon name="down" size={18} /></summary>
            <div className="crm-profile-fields">
            {[['preferences', '服务偏好', '记录客人已明确的服务需求'], ['avoidances', '避免安排', '记录服务安排上的注意事项'],
              ['notes', '内部备注', '仅管理员可见的必要补充']].map(([key, label, placeholder]) =>
              <label key={key}>{label}<textarea maxLength={2000} placeholder={placeholder} value={editor[key]} onChange={e => field(key, e.target.value)} /></label>)}
            </div>
            <p className="admin-hint">只记录必要服务信息，勿记录敏感私人资料。</p>
          </details>
        </> : editor.action === 'admin_consumption_save' ? <>
          <label>会所<select required value={editor.venue_id} onChange={e => field('venue_id', e.target.value)}>
            <option value="">请选择</option>{data?.venues.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select></label>
          <label>服务技师<select disabled={!editor.venue_id} value={editor.staff_id} onChange={e => field('staff_id', e.target.value)}>
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
          <details open={consumptionExtras} onToggle={event => setConsumptionExtras(event.currentTarget.open)}>
          <summary>已退款金额与内部备注（选填）</summary>
          <label>已经实际退款 USD<input inputMode="decimal" pattern="\d{1,10}(\.\d{1,2})?" value={editor.refunded} onChange={e => field('refunded', e.target.value)} /></label>
          <label>本次消费内部备注<textarea maxLength={2000} value={editor.note} onChange={e => field('note', e.target.value)} /></label>
          </details>
          <p className="admin-hint">这里记录线下实付及已退款事实，不执行任何资金操作。</p>
        </> : <p>作废后不计入累计消费，客户仍可看到作废状态，原记录不会删除。</p>}
        {!!editor.revision && <label className="crm-change-reason">操作原因<span className="crm-field-hint">说明本次修改、绑定或作废的原因，保留操作记录。</span>
          <textarea required maxLength={500} placeholder="简要说明本次操作原因" value={editor.reason} onChange={e => field('reason', e.target.value)} /></label>}
      </fieldset>}
      {confirmation && <section className="crm-confirm" aria-label="确认登记">
        <h3>{actions[confirmation.action]}</h3>
        {confirmation.action === 'admin_customer_bind' && <p>确认将本客户消费记录开放给 Telegram ID {confirmation.telegram_user_id}。请核实确为同一位客人。</p>}
        {confirmation.action === 'admin_customer_save' && <dl className="crm-preferences">
          <dt>客户称呼</dt><dd>{confirmation.alias || data?.customer.display_name || '使用 Telegram 名称'}</dd>
          {[['preferences', '服务偏好'], ['avoidances', '避免安排'], ['notes', '内部备注']].map(([key, label]) =>
            confirmation[key] && <div key={key}><dt>{label}</dt><dd>{confirmation[key]}</dd></div>)}
        </dl>}
        {confirmation.action === 'admin_consumption_save' && <p>
          {consumptionDate(confirmation.occurred_at)} · {confirmation.service}<br />
          {data?.venues.find(row => row.id === Number(confirmation.venue_id))?.name}
          {' · '}{confirmation.staff_id
            ? `工号 ${data?.staff.find(row => row.id === Number(confirmation.staff_id))?.code} · 档案${confirmation.staff_id}` : '未关联技师'}<br />
          实付 ${money(confirmation.amount)} · 已退 ${money(confirmation.refunded)}
        </p>}
        {confirmation.reason && <p>操作原因：{confirmation.reason}</p>}
        {confirmation.action === 'admin_consumption_void' && <p>作废后不再计入累计，原消费记录仍保留。</p>}
        <details><summary>操作编号</summary><small>{confirmation.request_id}</small></details>
        {uncertain && <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>刷新核对保存结果</button>}
      </section>}
    </form>
    </MusicDialog>}
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
  const [createOnOpen, setCreateOnOpen] = useState(false)
  const [newCustomer, setNewCustomer] = useState(false)
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
  if (userId) return <CustomerRecordDetail key={userId} userId={userId} createOnOpen={createOnOpen} onClose={() => setUserId(null)} />
  return <section className="admin-card">
    <div className="admin-header"><div><h2>客户管理</h2><p className="admin-hint">客户档案 · 消费与服务记录</p></div>
      <button type="button" className="crm-primary" onClick={() => setNewCustomer(true)}>＋ 新建客户</button></div>
    <p className="admin-hint">搜索客户，打开档案或直接建档。</p>
    <form className="crm-search" onSubmit={e => { e.preventDefault(); setPage(1); setSearch(query.trim()); setReload(value => value + 1) }}>
      <input aria-label="搜索客户" maxLength={128} value={query} onChange={e => setQuery(e.target.value)} placeholder="ID / @用户名 / 客户称呼" />
      <button disabled={busy} type="submit">搜索</button>
    </form>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {busy && <p role="status">正在读取…</p>}
    {data && <>
      <div className="crm-customer-list">
        {data.customers.map(row => <button key={row.user_id} type="button" className="crm-customer-item"
          onClick={() => { setCreateOnOpen(!row.registered); setUserId(row.user_id) }}>
          <span><strong>{identity(row)}</strong><small>{row.username ? `@${row.username} · ` : ''}{customerAccount(row)}</small>
            <small>{row.visits}笔消费 · 净累计 ${money(row.net)}</small></span>
          <span className="crm-customer-status">{row.registered ? '查看档案' : '建立档案'}</span>
        </button>)}
        {!data.customers.length && <p className="crm-empty">未找到客户。可点击“新建客户”先用称呼建档。</p>}
      </div>
      <ConsumptionPager page={page} total={data.total} onChange={setPage} busy={busy} />
    </>}
    {newCustomer && <NewCustomer onClose={() => setNewCustomer(false)} onCreated={id => {
      setNewCustomer(false); setCreateOnOpen(false); setUserId(id)
    }} />}
  </section>
}

function NewCustomer({ onClose, onCreated }) {
  const formRef = useRef(null)
  const key = `crm-offline-create-pending-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'telegram'}`
  const [alias, setAlias] = useState('')
  const [pending, setPending] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const lock = useRef(false)
  useEffect(() => {
    const saved = sessionStorage.getItem(key)
    if (!saved) return
    try {
      const payload = JSON.parse(saved)
      if (payload.action !== 'admin_customer_create' || !/^[0-9a-f]{32}$/.test(payload.request_id)) throw new Error('操作记录无效')
      setPending(payload); setAlias(payload.alias); setUncertain(true)
    } catch (failure) { console.error('[Restore new customer]', failure); setError('暂存操作无效，请先人工核对客户列表，不要重复建档'); setUncertain(true) }
  }, [])
  const save = async () => {
    if (lock.current || !pending) return
    lock.current = true; setBusy(true); setError('')
    try {
      sessionStorage.setItem(key, JSON.stringify(pending))
      const result = await requestAdmin('admin_customer_create', pending)
      sessionStorage.removeItem(key); onCreated(result.user_id)
    } catch (failure) {
      console.error('[Create offline customer]', failure); setError(failure.message)
      if ([400, 401, 403, 409].includes(failure.status)) {
        sessionStorage.removeItem(key); setPending(null); setUncertain(false)
      } else setUncertain(true)
    } finally { lock.current = false; setBusy(false) }
  }
  return <MusicDialog className="crm-editor-sheet" title="新建客户" onClose={() => {
    if (lock.current) return
    if (uncertain) { setError('保存结果尚未确认，请用原编号重试，避免重复建档'); return }
    onClose()
  }} scrollBody footer={<div className="crm-editor-actions">
    <button type="button" disabled={busy || uncertain} onClick={() => pending ? setPending(null) : onClose()}>
      {pending ? '返回修改' : '取消'}</button>
    <button type="button" className="crm-primary" disabled={busy || (!pending && (uncertain || !alias.trim()))}
      onClick={() => pending ? save() : formRef.current?.requestSubmit()}>
      {busy ? '保存中…' : pending ? uncertain ? '原编号重试' : '确认建档' : '下一步：核对'}</button>
  </div>}>
    <p className="admin-hint">无需关注 App 或提供 Telegram 账号，先保存客户称呼，以后再绑定。</p>
    {error && <p className="admin-error" role="alert">{error}</p>}
    {uncertain && <p className="admin-notice">本次结果未确认，请使用原编号重试。</p>}
    {!pending ? <form ref={formRef} className="crm-form" onSubmit={event => {
      event.preventDefault(); setPending({ action: 'admin_customer_create', alias: alias.trim(),
        preferences: '', avoidances: '', notes: '', revision: 0, request_id: newMemberOrderId(), confirmed: true })
    }}>
      <label>客户称呼<input required maxLength={128} value={alias} onChange={e => setAlias(e.target.value)} placeholder="方便识别的称呼" /></label>
    </form> : <>
      <h3>{pending.alias}</h3><p>建立独立线下档案，不关联任何 Telegram 账号，也不会生成消费。</p>
    </>}
  </MusicDialog>
}
