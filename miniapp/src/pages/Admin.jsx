import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { bindBackButton } from '../utils/twa'
import AccountHeader from '../components/AccountHeader'
import { isLocalAdminDebug, localDateInput, requestAdmin, tenantLink, utcExpiry } from '../utils/admin'
import DepositReview from '../components/DepositReview'
import CreditedCustomers from '../components/CreditedCustomers'
import HotelAdmin from '../components/HotelAdmin'
import VenueRecommendationAdmin from '../components/VenueRecommendationAdmin'
import CustomerAnalytics from '../components/CustomerAnalytics'
import StaffAdmin from '../components/StaffAdmin'
import CustomerRecords from '../components/CustomerRecords'
import OperationsHealth from '../components/OperationsHealth'
import MusicDialog from '../components/MusicDialog'
import './Membership.css'
import './Admin.css'

const labels = { draft: '草稿', active: '已启用', suspended: '已暂停', expired: '已到期' }
const auditFields = [
  ['slug', '入口标识'],
  ['brand_name', '品牌名称'],
  ['contact_name', '联系名称'],
  ['contact_username', '联系用户名'],
  ['venue_ids', '展示项目'],
  ['status', '服务状态'],
  ['expires_at', '到期时间'],
  ['announcements_enabled', '频道公告'],
]
const empty = () => ({
  slug: '', brand_name: '', contact_name: '', contact_username: '',
  venue_ids: [], status: 'draft', expires_at: '', announcements_enabled: false,
})
const formatDate = iso => iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '未设置'

function auditValue(key, value, venues) {
  if (key === 'status') return labels[value] || value || '未设置'
  if (key === 'contact_username') return value ? `@${value}` : '未设置'
  if (key === 'venue_ids') {
    return (value || []).map(id => venues.find(venue => venue.id === id)?.name || `项目 ${id}`).join('、') || '无'
  }
  if (key === 'expires_at') return formatDate(value)
  if (key === 'announcements_enabled') return value ? '已开放' : '未开放'
  return value || '未设置'
}

function tenantAuditChanges(row, venues) {
  const before = row.before_config || {}
  const after = row.after_config || {}
  return auditFields
    .filter(([key]) => row.action === 'create' ||
      JSON.stringify(before[key] ?? null) !== JSON.stringify(after[key] ?? null))
    .map(([key, label]) => ({
      key,
      label,
      before: auditValue(key, before[key], venues),
      after: auditValue(key, after[key], venues),
    }))
}

export default function Admin() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const customerParam = searchParams.get('customer')
  const initialCustomerId = /^-?[1-9]\d{0,15}$/.test(customerParam || '') && Number.isSafeInteger(Number(customerParam))
    ? Number(customerParam) : null
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [editor, setEditor] = useState(null)
  const [confirmation, setConfirmation] = useState(null)
  const [copyFallback, setCopyFallback] = useState('')
  const [section, setSection] = useState('customers')
  const [tenantSearch, setTenantSearch] = useState('')
  const [tenantStatus, setTenantStatus] = useState('all')
  const [tenantDetail, setTenantDetail] = useState(null)
  useEffect(() => { if (initialCustomerId) setSection('customers') }, [initialCustomerId])
  const inFlight = useRef(false)
  const mounted = useRef(true)
  const formRef = useRef(null)
  const dashboardRequestId = useRef(0)
  const tenantPagination = data?.tenant_pagination || {
    page: 1,
    page_size: data?.tenants?.length || 50,
    filtered_total: data?.tenants?.length || 0,
    total_pages: 1,
    search: '',
    status: 'all',
  }

  const refresh = async (page = data?.tenant_pagination?.page || 1,
                         search = tenantSearch, status = tenantStatus) => {
    const requestId = ++dashboardRequestId.current
    setLoading(true)
    setError('')
    try {
      const result = await requestAdmin('admin_dashboard', {
        tenant_page: page,
        tenant_search: search.trim(),
        tenant_status: status,
      })
      if (mounted.current && requestId === dashboardRequestId.current) {
        setData(result)
      }
    } catch (failure) {
      if (!mounted.current || requestId !== dashboardRequestId.current) return
      if ([401, 403].includes(failure.status)) {
        setData(null)
        setEditor(null)
        setConfirmation(null)
        setTenantDetail(null)
      }
      setError(failure.message)
    } finally {
      if (mounted.current && requestId === dashboardRequestId.current) setLoading(false)
    }
  }
  useEffect(() => {
    mounted.current = true
    refresh()
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    if (!data) return undefined
    const timer = window.setTimeout(() => refresh(1, tenantSearch, tenantStatus), 300)
    return () => window.clearTimeout(timer)
  }, [tenantSearch, tenantStatus])
  useEffect(() => bindBackButton(() => {
    if (saving) return
    if (confirmation) setConfirmation(null)
    else if (editor) setEditor(null)
    else navigate('/personal', { replace: true })
  }), [navigate, editor, confirmation, saving])
  useEffect(() => {
    if (editor) formRef.current?.scrollIntoView({ block: 'start' })
  }, [editor?.original?.slug, Boolean(editor)])

  const edit = row => {
    setTenantDetail(null)
    setNotice('')
    setError('')
    setConfirmation(null)
    setEditor({
      original: row || null,
      values: row ? { ...row, expires_at: localDateInput(row.expires_at) } : empty(),
    })
  }
  const field = (key, value) => {
    setConfirmation(null)
    setEditor(current => ({ ...current, values: { ...current.values, [key]: value } }))
  }
  const prepareSave = event => {
    event.preventDefault()
    setError('')
    try {
      const value = editor.values
      if (!value.venue_ids.length) throw new Error('请至少选择一个会所或外送项目')
      const expires_at = editor.original && value.expires_at === localDateInput(editor.original.expires_at)
        ? editor.original.expires_at : utcExpiry(value.expires_at)
      if (value.status === 'active' && (!expires_at || new Date(expires_at) <= new Date())) {
        throw new Error('启用或续期时请选择未来的到期时间')
      }
      setConfirmation({
        mode: editor.original ? 'update' : 'create',
        revision: editor.original?.revision,
        tenant: {
          slug: value.slug.trim(), brand_name: value.brand_name.trim(),
          contact_name: value.contact_name.trim(),
          contact_username: value.contact_username.trim().replace(/^@/, ''),
          venue_ids: value.venue_ids, status: value.status, expires_at,
          announcements_enabled: Boolean(value.announcements_enabled),
        },
      })
    } catch (failure) { setError(failure.message) }
  }
  const save = async () => {
    if (inFlight.current || !confirmation) return
    inFlight.current = true
    setSaving(true)
    setError('')
    setNotice('')
    try {
      await requestAdmin('admin_tenant_save', confirmation)
      setEditor(null)
      setConfirmation(null)
      setNotice('租户配置已保存。未执行收费或扣款。')
      await refresh(confirmation.mode === 'create' ? 1 : data?.tenant_pagination.page || 1)
    } catch (failure) {
      if ([401, 403].includes(failure.status)) {
        setData(null)
        setEditor(null)
        setConfirmation(null)
      }
      setError(failure.message)
      if (![400, 401, 403, 409].includes(failure.status)) {
        setNotice('未能确认保存结果，请先刷新核对配置和操作记录，不要立即重复提交。')
      }
      // Never retry a write automatically after an uncertain response.
      setConfirmation(null)
    } finally {
      inFlight.current = false
      setSaving(false)
    }
  }
  const copy = async slug => {
    const link = tenantLink(slug)
    setCopyFallback('')
    setNotice('')
    try {
      await navigator.clipboard.writeText(link)
      setNotice('专属入口已复制')
    } catch (failure) {
      console.error('[Copy tenant link]', failure)
      setCopyFallback(link)
      setNotice('自动复制失败，请长按下方链接手动复制')
    }
  }
  const tenantExpiry = row => {
    if (!row.expires_at) return '未设到期时间'
    if (row.effective_status === 'expired') return '已到期'
    const days = Math.ceil((new Date(row.expires_at) - Date.now()) / 86400000)
    return days > 0 ? `剩余 ${days} 天` : '已过到期时间'
  }

  return (
    <main className="admin-console" aria-label="运营管理台">
      <AccountHeader>
      {data && <nav className="admin-sections" aria-label="管理功能">
        {[['customers', '客户'], ['staff', '人员'], ['analytics', '分析']].map(([key, label]) =>
          <button key={key} type="button" aria-pressed={section === key} disabled={saving}
            onClick={() => setSection(key)}>{label}</button>)}
        <label className="admin-more"><span className="music-sr-only">其他管理功能</span>
          <select aria-label="其他管理功能" disabled={saving} value={['deposits', 'credited', 'hotels', 'recommendations', 'tenants', 'operations', 'finance'].includes(section) ? section : ''}
            onChange={event => {
              if (event.target.value === 'finance') navigate('/personal-finance?from=admin')
              else if (event.target.value) setSection(event.target.value)
            }}>
            <option value="">更多管理</option>
            <option value="deposits">充值复核（{data.counts.pending_deposits}待核）</option>
            <option value="credited">已充值用户</option>
            <option value="hotels">酒店推荐管理</option>
            <option value="recommendations">名玺推荐管理</option>
            <option value="tenants">租户管理</option>
            <option value="operations">运营健康</option>
            <option value="finance">个人财务</option>
          </select>
        </label>
      </nav>}
      </AccountHeader>
      {isLocalAdminDebug() && <p role="status" className="admin-notice">
        本机开发调试已启用：无 Telegram 身份时使用调试账号，修改会写入当前数据库，请勿操作真实充值或客户记录。
      </p>}
      {error && <p role="alert" className="admin-error">{error}
        <button type="button" disabled={loading || saving} onClick={refresh}>重新读取</button></p>}
      {notice && <p role="status" className="admin-notice">{notice}</p>}
      {copyFallback && <input aria-label="专属入口链接" readOnly value={copyFallback} onFocus={event => event.target.select()} />}
      {!data && !loading && !error && <p>暂无管理数据</p>}
      {data && <>
        {section === 'analytics' && <CustomerAnalytics />}
        {section === 'customers' && <CustomerRecords key={initialCustomerId || 'directory'} initialUserId={initialCustomerId} />}
        {section === 'staff' && <StaffAdmin venues={data.venues} />}
        {section === 'credited' && <CreditedCustomers />}
        {section === 'hotels' && <HotelAdmin />}
        {section === 'recommendations' && <VenueRecommendationAdmin />}
        {section === 'operations' && <OperationsHealth />}
        <div hidden={section !== 'deposits'}><DepositReview onReviewed={refresh} /></div>
        <div hidden={section !== 'tenants'}>
        <section className="admin-tenant-directory" hidden={Boolean(editor)}>
          <div className="admin-header"><h2>租户管理</h2><button type="button" disabled={saving || loading} onClick={() => edit(null)}>＋ 新建</button></div>
          <nav aria-label="按状态筛选租户" className="admin-tenant-statuses">
            {[
              ['all', '全部', data.counts.total], ['active', '已启用', data.counts.active],
              ['expiring', '7天内到期', data.counts.expiring], ['expired', '已到期', data.counts.expired],
              ['draft', '草稿', data.counts.draft], ['suspended', '已暂停', data.counts.suspended],
            ].map(([status, label, count]) => <button key={status} type="button"
              aria-pressed={tenantStatus === status} disabled={saving}
              onClick={() => { setTenantSearch(''); setTenantStatus(status) }}>
              <span>{label}</span><strong>{count}</strong>
            </button>)}
          </nav>
          <label className="admin-tenant-search"><span className="music-sr-only">搜索租户</span>
            <input type="search" aria-label="搜索租户" placeholder="搜索名称、入口或联系人"
              maxLength={100} value={tenantSearch} onChange={event => setTenantSearch(event.target.value)} />
          </label>
          <p role="status" className="admin-hint">{loading ? '正在筛选租户…' : `共 ${tenantPagination.filtered_total} 个租户`}</p>
          {data.tenants.length === 0 && tenantPagination.filtered_total === 0 &&
            <p>{data.counts.total === 0 ? '暂无租户，可先创建草稿。' : '没有符合条件的租户。'}</p>}
          <div className="admin-tenant-list" aria-busy={loading}>
            {data.tenants.map(row => <button key={row.slug} type="button"
              className="admin-tenant-card" disabled={saving || loading} onClick={() => setTenantDetail(row)}>
              <span className="admin-tenant-card-heading"><strong>{row.brand_name}</strong>
                <span className={`admin-tenant-badge is-${row.effective_status}`}>{labels[row.effective_status]}</span></span>
              <span className="admin-tenant-contact">{row.contact_name} · @{row.contact_username}</span>
              <span className="admin-tenant-card-footer"><span>到期：{formatDate(row.expires_at)} · {tenantExpiry(row)}</span>
                <span>查看详情 ›</span></span>
            </button>)}
          </div>
          {tenantPagination.total_pages > 1 && <nav className="admin-tenant-pagination" aria-label="租户列表分页">
            <button type="button" disabled={loading || tenantPagination.page <= 1}
              onClick={() => refresh(tenantPagination.page - 1)}>上一页</button>
            <span>{tenantPagination.page} / {tenantPagination.total_pages}</span>
            <button type="button" disabled={loading || tenantPagination.page >= tenantPagination.total_pages}
              onClick={() => refresh(tenantPagination.page + 1)}>下一页</button>
          </nav>}
        </section>
        {tenantDetail && section === 'tenants' && <MusicDialog title="租户详情"
          className="admin-tenant-dialog" scrollBody onClose={() => setTenantDetail(null)}
          footer={<div className="admin-tenant-detail-actions">
            <button type="button" onClick={() => copy(tenantDetail.slug)}>复制入口</button>
            <button type="button" onClick={() => edit(tenantDetail)}>编辑 / 续期 / 暂停</button>
          </div>}>
          <h3>{tenantDetail.brand_name}</h3>
          <dl className="admin-tenant-detail">
            <div><dt>服务状态</dt><dd>{labels[tenantDetail.effective_status]}</dd></div>
            <div><dt>入口标识</dt><dd>{tenantDetail.slug}</dd></div>
            <div><dt>联系人</dt><dd>{tenantDetail.contact_name} · @{tenantDetail.contact_username}</dd></div>
            <div><dt>到期时间</dt><dd>{formatDate(tenantDetail.expires_at)} · {tenantExpiry(tenantDetail)}</dd></div>
            <div><dt>展示权限</dt><dd>{tenantDetail.venue_ids.map(id => data.venues.find(venue => venue.id === id)?.name || `项目 ${id}`).join('、')}</dd></div>
            <div><dt>频道公告</dt><dd>{tenantDetail.announcements_enabled ? '已开放' : '未开放'}</dd></div>
          </dl>
          {tenantDetail.effective_status !== 'active' && <p className="admin-hint">当前入口不可用，启用且未到期后才能访问。</p>}
          <p className="admin-hint">共享机器人及公共观影频道，客户与频道成员并非完全隔离。</p>
          {notice && <p role="status" className="admin-notice">{notice}</p>}
          {copyFallback && <label>专属入口链接<input readOnly value={copyFallback}
            onFocus={event => event.target.select()} /></label>}
        </MusicDialog>}
        {editor && (
          <section ref={formRef} className="admin-card">
            <h2>{editor.original ? `编辑 ${editor.original.slug}` : '新建租户（默认草稿）'}</h2>
            <form onSubmit={prepareSave}>
              <fieldset disabled={saving || loading}>
                <label>入口标识<input required minLength={3} maxLength={32} pattern="[a-z0-9][a-z0-9-]{2,31}" disabled={Boolean(editor.original)}
                  value={editor.values.slug} onChange={event => field('slug', event.target.value)} /></label>
                <p className="admin-hint">3–32 位小写字母、数字或连字符，创建后不可改名。</p>
                <label>品牌名称<input required maxLength={80} value={editor.values.brand_name} onChange={event => field('brand_name', event.target.value)} /></label>
                <label>联系名称<input required maxLength={40} value={editor.values.contact_name} onChange={event => field('contact_name', event.target.value)} /></label>
                <label>Telegram 联系用户名<input required maxLength={33} value={editor.values.contact_username} onChange={event => field('contact_username', event.target.value)} placeholder="不含 @，也可粘贴 @用户名" /></label>
                {[{ title: '会所权限', delivery: false }, { title: '外送权限（默认关闭，按需勾选）', delivery: true }].map(group => (
                <fieldset key={group.title}><legend>{group.title}</legend>
                  {data.venues.filter(venue => (venue.id >= 7) === group.delivery).map(venue => (
                    <label key={venue.id} className="admin-check"><input type="checkbox" checked={editor.values.venue_ids.includes(venue.id)}
                      onChange={event => field('venue_ids', event.target.checked ? [...editor.values.venue_ids, venue.id] : editor.values.venue_ids.filter(id => id !== venue.id))} />{venue.name}</label>
                  ))}
                </fieldset>
                ))}
                <label className="admin-check"><input type="checkbox" checked={Boolean(editor.values.announcements_enabled)}
                  onChange={event => field('announcements_enabled', event.target.checked)} />开放频道公告、详情与历史列表（默认关闭）</label>
                <div>
                  {editor.values.venue_ids.filter(id => !data.venues.some(venue => venue.id === id)).map(id => (
                    <label key={id} className="admin-check"><input type="checkbox" checked onChange={() => field('venue_ids', editor.values.venue_ids.filter(value => value !== id))} />项目 {id} 已停用，请取消选择</label>
                  ))}
                </div>
                <label>服务状态<select value={editor.values.status} onChange={event => field('status', event.target.value)}>
                  <option value="draft">草稿（不开放）</option><option value="active">启用</option><option value="suspended">暂停</option>
                </select></label>
                <label>到期时间（本机当地时间）<input type="datetime-local" step="60" value={editor.values.expires_at} onChange={event => field('expires_at', event.target.value)} /></label>
                <p className="admin-hint">当前时区：{Intl.DateTimeFormat().resolvedOptions().timeZone}。续期请修改到期时间并选择启用；到期后自动不可用，不删除数据。</p>
                <div className="admin-actions"><button type="submit">核对并保存</button><button type="button" onClick={() => { setEditor(null); setConfirmation(null) }}>取消编辑</button></div>
              </fieldset>
            </form>
            {confirmation && <div role="region" aria-label="保存前确认" className="admin-confirm">
              <h3>确认配置</h3>
              <p>{confirmation.tenant.brand_name} · {labels[confirmation.tenant.status]}</p>
              <p>联系人：{confirmation.tenant.contact_name} / @{confirmation.tenant.contact_username}</p>
              <p>到期：{formatDate(confirmation.tenant.expires_at)}</p>
              <p>展示项目：{confirmation.tenant.venue_ids.map(id => data.venues.find(venue => venue.id === id)?.name || id).join('、')} · 公告{confirmation.tenant.announcements_enabled ? '已开放' : '未开放'}</p>
              <p>启用、暂停及续期会影响入口访问。本操作不收款、不扣款。</p>
              <div className="admin-actions"><button type="button" disabled={saving} onClick={save}>{saving ? '保存中…' : '确认保存'}</button><button type="button" disabled={saving} onClick={() => setConfirmation(null)}>返回修改</button></div>
            </div>}
          </section>
        )}
        <details className="admin-card admin-tenant-audit"><summary>最近操作与配置变更</summary>
          <p className="admin-hint">最近30条 · {formatDate(data.server_time)}</p>
          {data.audit.length === 0 && <p>暂无操作记录</p>}
          {data.audit.map(row => {
            const changes = tenantAuditChanges(row, data.venues)
            return <article className="admin-audit" key={row.id}>
              <p>{formatDate(row.created_at)} · {row.tenant_slug} · {row.action === 'create' ? '创建' : '更新'} · {row.source === 'cli' ? '本机命令行' : `管理员 ${row.actor_id}`}</p>
              <details className="admin-audit-details">
                <summary>{row.action === 'create' ? '查看初始配置' : `查看配置变更（${changes.length} 项）`}</summary>
                {changes.length
                  ? <ul>{changes.map(change => <li key={change.key}>
                    <strong>{change.label}：</strong>
                    {row.action === 'create' ? change.after : `${change.before} → ${change.after}`}
                  </li>)}</ul>
                  : <p>本次操作未更改配置字段。</p>}
              </details>
            </article>
          })}
        </details>
        </div>
      </>}
    </main>
  )
}
