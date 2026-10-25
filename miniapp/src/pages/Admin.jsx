import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { bindBackButton, hasTelegramBackButton, subscribeFullscreen } from '../utils/twa'
import { FullscreenAnnouncementBar } from '../components/Announcement'
import { localDateInput, requestAdmin, tenantLink, utcExpiry } from '../utils/admin'
import './Admin.css'

const labels = { draft: '草稿', active: '已启用', suspended: '已暂停', expired: '已到期' }
const empty = () => ({
  slug: '', brand_name: '', contact_name: '', contact_username: '',
  venue_ids: [], status: 'draft', expires_at: '',
})
const formatDate = iso => iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '未设置'

export default function Admin() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [editor, setEditor] = useState(null)
  const [confirmation, setConfirmation] = useState(null)
  const [copyFallback, setCopyFallback] = useState('')
  const [isFullscreen, setIsFullscreen] = useState(false)
  const inFlight = useRef(false)
  const mounted = useRef(true)
  const formRef = useRef(null)

  const refresh = async () => {
    setLoading(true)
    setError('')
    try {
      const result = await requestAdmin('admin_dashboard')
      if (mounted.current) setData(result)
    } catch (failure) {
      if (!mounted.current) return
      if ([401, 403].includes(failure.status)) {
        setData(null)
        setEditor(null)
        setConfirmation(null)
      }
      setError(failure.message)
    } finally {
      if (mounted.current) setLoading(false)
    }
  }
  useEffect(() => {
    mounted.current = true
    refresh()
    return () => { mounted.current = false }
  }, [])
  useEffect(() => subscribeFullscreen(setIsFullscreen), [])
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
      if (!value.venue_ids.length) throw new Error('请至少选择一个会所')
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
      await refresh()
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

  return (
    <main className="admin-console">
      {isFullscreen && <FullscreenAnnouncementBar />}
      <header className="admin-header">
        {!hasTelegramBackButton && <button type="button" disabled={saving} onClick={() => navigate('/personal', { replace: true })}>← 返回</button>}
        <h1>运营管理台</h1>
        <button type="button" disabled={loading || saving} onClick={refresh}>{loading ? '读取中…' : '刷新'}</button>
      </header>
      <p className="admin-hint">仅平台管理员 · 人工收月费 · 充值审核仍使用机器人 /wallet</p>
      {error && <p role="alert" className="admin-error">{error}</p>}
      {notice && <p role="status" className="admin-notice">{notice}</p>}
      {copyFallback && <input aria-label="专属入口链接" readOnly value={copyFallback} onFocus={event => event.target.select()} />}
      {!data && !loading && !error && <p>暂无管理数据</p>}
      {data && <>
        <section aria-label="运营概览" className="admin-stats">
          {[
            ['租户总数', data.counts.total], ['已启用', data.counts.active],
            ['7天内到期', data.counts.expiring], ['已到期', data.counts.expired],
            ['草稿 / 暂停', `${data.counts.draft} / ${data.counts.suspended}`],
            ['待核充值', data.counts.pending_deposits],
          ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}
        </section>
        <p className="admin-hint">统计时间：{formatDate(data.server_time)}。待核充值仅显示数量，本页不操作资金。</p>
        <section className="admin-card">
          <div className="admin-header"><h2>租户管理</h2><button type="button" disabled={saving || loading} onClick={() => edit(null)}>新建租户</button></div>
          <p className="admin-hint">共享机器人及公共观影频道；客户与频道成员并非完全隔离。</p>
          {data.tenants.length === 0 && <p>暂无租户，可先创建草稿。</p>}
          {data.tenants.map(row => (
            <article key={row.slug} className="admin-tenant">
              <div className="admin-header"><h3>{row.brand_name}</h3><span>{labels[row.effective_status]}</span></div>
              <p>{row.slug} · @{row.contact_username}</p>
              <p>到期：{formatDate(row.expires_at)}</p>
              <p>会所：{row.venue_ids.map(id => data.venues.find(venue => venue.id === id)?.name || `会所 ${id}`).join('、')}</p>
              <div className="admin-actions">
                <button type="button" disabled={saving || loading} onClick={() => edit(row)}>编辑 / 续期 / 暂停</button>
                <button type="button" disabled={saving} onClick={() => copy(row.slug)}>复制入口</button>
              </div>
              {row.effective_status !== 'active' && <p className="admin-hint">当前入口不可用，启用且未到期后才能访问。</p>}
            </article>
          ))}
        </section>
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
                <fieldset><legend>展示会所</legend>
                  {data.venues.map(venue => (
                    <label key={venue.id} className="admin-check"><input type="checkbox" checked={editor.values.venue_ids.includes(venue.id)}
                      onChange={event => field('venue_ids', event.target.checked ? [...editor.values.venue_ids, venue.id] : editor.values.venue_ids.filter(id => id !== venue.id))} />{venue.name}</label>
                  ))}
                  {editor.values.venue_ids.filter(id => !data.venues.some(venue => venue.id === id)).map(id => (
                    <label key={id} className="admin-check"><input type="checkbox" checked onChange={() => field('venue_ids', editor.values.venue_ids.filter(value => value !== id))} />会所 {id} 已停用，请取消选择</label>
                  ))}
                </fieldset>
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
              <p>会所：{confirmation.tenant.venue_ids.map(id => data.venues.find(venue => venue.id === id)?.name || id).join('、')}</p>
              <p>启用、暂停及续期会影响入口访问。本操作不收款、不扣款。</p>
              <div className="admin-actions"><button type="button" disabled={saving} onClick={save}>{saving ? '保存中…' : '确认保存'}</button><button type="button" disabled={saving} onClick={() => setConfirmation(null)}>返回修改</button></div>
            </div>}
          </section>
        )}
        <section className="admin-card"><h2>最近操作</h2>
          <p className="admin-hint">显示最近 30 条；创建、修改、续期、暂停均记录操作者和前后配置。</p>
          {data.audit.length === 0 && <p>暂无操作记录</p>}
          {data.audit.map(row => <p className="admin-audit" key={row.id}>
            {formatDate(row.created_at)} · {row.tenant_slug} · {row.action === 'create' ? '创建' : '更新'} · {row.source === 'cli' ? '本机命令行' : `管理员 ${row.actor_id}`}
          </p>)}
        </section>
      </>}
    </main>
  )
}
