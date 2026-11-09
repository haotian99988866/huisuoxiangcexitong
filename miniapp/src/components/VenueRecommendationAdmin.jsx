import { useEffect, useRef, useState } from 'react'
import { localDateInput, requestAdmin, utcExpiry } from '../utils/admin'
import { recommendationDraft, recommendationKinds, saveRecommendation } from '../utils/venueRecommendations'
import MusicDialog from './MusicDialog'
import './HotelAdmin.css'
import './VenueRecommendationAdmin.css'

const newId = () => crypto.randomUUID().replaceAll('-', '')
const draftKey = () => `venue-2-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'telegram'}`
const dateText = value => new Date(value).toLocaleString('zh-CN', { hour12: false })
const editorValues = row => ({ ...row, starts_at: localDateInput(row.starts_at), ends_at: localDateInput(row.ends_at) })
const status = row => !row.enabled ? '已下架' : Date.parse(row.ends_at) <= Date.now() ? '已过期'
  : Date.parse(row.starts_at) > Date.now() ? '待开始' : '展示中'

export default function VenueRecommendationAdmin() {
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [reload, setReload] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [editor, setEditor] = useState(null)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [pending, setPending] = useState(null)
  const [busy, setBusy] = useState(false)
  const [recovering, setRecovering] = useState(true)
  const [recoveryError, setRecoveryError] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const lock = useRef(false)
  const mounted = useRef(false)
  const form = useRef(null)
  useEffect(() => {
    mounted.current = true
    recommendationDraft(draftKey(), 'get').then(saved => {
      if (!mounted.current || !saved) return
      if (!saved.payload || saved.payload.venue_id !== 2 ||
          !/^[0-9a-f]{32}$/.test(saved.payload.id) || !/^[0-9a-f]{32}$/.test(saved.payload.request_id) ||
          (saved.image !== null && !(saved.image instanceof Blob))) throw new Error('暂存推荐操作无效')
      setEditor(editorValues(saved.payload)); setFile(saved.image); setPending(saved); setUncertain(true)
    }).catch(failure => {
      console.error('[Restore recommendation]', failure)
      if (mounted.current) setRecoveryError(`推荐暂存读取失败：${failure.message}，请先人工核对，不要重复新增。`)
    }).finally(() => { if (mounted.current) setRecovering(false) })
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    requestAdmin('admin_venue_recommendations', { venue_id: 2, page }).then(result => {
      if (active) setData(result)
    }).catch(failure => {
      console.error('[Admin recommendations]', failure)
      if (active) { setData(null); setError(failure.message) }
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [page, reload])
  useEffect(() => {
    setPreview(''); setPreviewError('')
    if (!editor) return
    if (file) {
      const url = URL.createObjectURL(file)
      setPreview(url)
      return () => URL.revokeObjectURL(url)
    }
    if (!editor.revision) return
    let active = true
    let url = ''
    requestAdmin('admin_venue_recommendation_image', { id: editor.id }, { blob: true }).then(result => {
      if (active) { url = URL.createObjectURL(result); setPreview(url) }
    }).catch(failure => {
      console.error('[Recommendation preview]', failure)
      if (active) setPreviewError(failure.message)
    })
    return () => { active = false; if (url) URL.revokeObjectURL(url) }
  }, [editor?.id, file])
  const open = row => {
    if (busy || recovering || recoveryError || uncertain) return
    setError(''); setNotice(''); setFile(null); setPending(null)
    setEditor(row ? editorValues(row) : {
      id: newId(), revision: 0, venue_id: 2, kind: 'activity', title: '', description: '', enabled: true,
      starts_at: localDateInput(new Date().toISOString()),
      ends_at: localDateInput(new Date(Date.now() + 7 * 86400000).toISOString()),
    })
  }
  const close = () => {
    if (lock.current || uncertain) return
    if (pending) { setPending(null); return }
    setEditor(null); setFile(null)
  }
  const prepare = event => {
    event.preventDefault()
    setError('')
    try {
      if (!editor.revision && !file) throw new Error('新增推荐请上传照片或视频')
      const starts_at = utcExpiry(editor.starts_at), ends_at = utcExpiry(editor.ends_at)
      if (!starts_at || !ends_at || Date.parse(starts_at) >= Date.parse(ends_at)) throw new Error('结束时间须晚于开始时间')
      if (editor.enabled && Date.parse(ends_at) <= Date.now()) throw new Error('上架推荐须设置未来的结束时间')
      setPending({ payload: {
        id: editor.id, revision: editor.revision, venue_id: 2, kind: editor.kind,
        title: editor.title.trim(), description: editor.description.trim(), starts_at, ends_at,
        enabled: editor.enabled, confirmed: true, request_id: newId(),
      }, image: file })
    } catch (failure) { setError(failure.message) }
  }
  const save = async () => {
    if (lock.current || !pending) return
    lock.current = true; setBusy(true); setError('')
    let sent = false
    try {
      await recommendationDraft(draftKey(), 'put', pending)
      sent = true
      await saveRecommendation(pending.payload, pending.image)
      await recommendationDraft(draftKey(), 'delete')
      if (mounted.current) {
        setEditor(null); setFile(null); setPending(null); setUncertain(false)
        setNotice(pending.payload.enabled ? '推荐已保存，将在有效期内展示。' : '推荐已下架，客人端及媒体入口停止展示。')
        setReload(value => value + 1)
      }
    } catch (failure) {
      console.error('[Save recommendation]', failure)
      if (!mounted.current) return
      setError(failure.message)
      if ([400, 401, 403, 409, 413].includes(failure.status)) {
        try {
          await recommendationDraft(draftKey(), 'delete')
          setPending(null); setUncertain(false)
          if ([401, 403, 409].includes(failure.status)) { setEditor(null); setReload(value => value + 1) }
        } catch (storageFailure) {
          console.error('[Clear recommendation draft]', storageFailure)
          setUncertain(true); setError(`暂存清理失败：${storageFailure.message}；请先核对保存结果`)
        }
      } else if (sent) setUncertain(true)
    } finally { lock.current = false; if (mounted.current) setBusy(false) }
  }
  const disabled = busy || recovering || Boolean(recoveryError) || Boolean(editor)
  const field = (name, value) => setEditor(current => ({ ...current, [name]: value }))
  const isVideo = file ? file.type.startsWith('video/') || /\.(mp4|mov|webm)$/i.test(file.name || '') : editor?.media_type === 'video'
  return <section className="admin-card hotel-admin recommendation-admin" aria-label="共享推荐管理">
    <div className="hotel-admin-heading"><div><h2>共享推荐管理</h2></div>
      <button className="hotel-admin-add" type="button" disabled={disabled} onClick={() => open(null)}>＋ 新增推荐</button></div>
    <p className="hotel-admin-intro">发布会所活动或已确认的新人推荐，按有效期自动展示及下架。</p>
    <p className="hotel-admin-guidance">名玺会所、凯迪、大西洋、帝豪及西港外送共用此推荐列表，名玺按摩和KTV不展示。请注明适用会所，核实适用条件、人员成年及本人授权、媒体使用许可；不会从采集照片自动认定新人。</p>
    {recoveryError && <p className="admin-error" role="alert">{recoveryError}</p>}
    {!editor && error && <p className="admin-error" role="alert">{error}</p>}
    {notice && <p className="admin-notice" role="status">{notice}</p>}
    <div className="hotel-admin-toolbar"><span>共 {data?.total ?? '—'} 条推荐</span>
      <button type="button" disabled={loading || disabled} onClick={() => setReload(value => value + 1)}>刷新</button></div>
    {loading && <p role="status">正在读取推荐…</p>}
    {data && !data.recommendations.length && <p className="hotel-admin-empty">暂无推荐，客人端不显示空白轮播。</p>}
    <div className="hotel-admin-list">{data?.recommendations.map(row => <article className="hotel-admin-item" key={row.id}>
      <div className="hotel-admin-item-heading"><div><h3>{row.title}</h3><span className="hotel-admin-status">{status(row)}</span></div></div>
      <p className="staff-meta">{recommendationKinds[row.kind]} · {dateText(row.starts_at)} — {dateText(row.ends_at)}</p>
      <p className="recommendation-admin-description">{row.description}</p>
      <div className="hotel-admin-actions">
        <button type="button" disabled={disabled} onClick={() => open(row)}>编辑推荐</button>
        <button type="button" disabled={disabled} onClick={() => open({ ...row, enabled: !row.enabled })}>
          {row.enabled ? '下架推荐' : '重新上架'}</button>
      </div>
    </article>)}</div>
    {data?.total > 20 && <nav className="hotel-admin-actions" aria-label="推荐管理分页">
      <button type="button" disabled={disabled || page === 1} onClick={() => setPage(value => value - 1)}>上一页</button>
      <span>{page} / {Math.ceil(data.total / 20)}</span>
      <button type="button" disabled={disabled || page * 20 >= data.total} onClick={() => setPage(value => value + 1)}>下一页</button>
    </nav>}
    {editor && <MusicDialog className="hotel-editor-sheet recommendation-editor-sheet" title={editor.revision ? '编辑共享推荐' : '新增共享推荐'}
      scrollBody onClose={close} footer={<div className="hotel-admin-actions">
        <button type="button" disabled={busy || uncertain} onClick={close}>{pending ? '返回修改' : '取消'}</button>
        <button type="button" className="hotel-admin-primary" disabled={busy} onClick={() => pending ? save() : form.current?.requestSubmit()}>
          {busy ? '保存中…' : uncertain ? '原编号重试' : pending ? '确认保存' : '下一步：核对'}</button>
      </div>}>
      {error && <p className="admin-error" role="alert">{error}</p>}
      {uncertain && <p className="admin-hint">保存结果未确认，资料与媒体已暂存；请使用原编号重试，不要重复新增。</p>}
      {preview && (isVideo ? <video className="hotel-admin-preview" src={preview} controls playsInline preload="metadata"
        aria-label="推荐视频预览" onError={() => setPreviewError('推荐视频预览失败，请选择有效视频')} />
        : <img className="hotel-admin-preview" src={preview} alt="推荐图预览" onError={() => {
        setPreview(''); setPreviewError('推荐图预览失败，请选择有效图片')
      }} />)}
      {previewError && <p className="admin-error" role="alert">{previewError}</p>}
      {!pending ? <form ref={form} className="hotel-editor-form" onSubmit={prepare}><fieldset disabled={busy}>
        <label>推荐类型<select value={editor.kind} onChange={event => field('kind', event.target.value)}>
          {Object.entries(recommendationKinds).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>推荐标题<input required maxLength={80} value={editor.title} onChange={event => field('title', event.target.value)} /></label>
        <label>推荐照片／视频<input type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm,.mov" required={!editor.revision} onChange={event => {
          const chosen = event.target.files?.[0]
          const video = chosen && (chosen.type.startsWith('video/') || /\.(mp4|mov|webm)$/i.test(chosen.name))
          if (chosen && chosen.size > (video ? 20 : 5) * 1024 * 1024) {
            setError(video ? '推荐视频不能超过20MB' : '推荐照片不能超过5MB'); event.target.value = ''; setFile(null); return
          }
          setError(''); setFile(chosen || null)
        }} /></label>
        <p className="admin-hint">照片JPG／PNG／WEBP，最多5MB、2000万像素；视频MP4／MOV／WebM，最多20MB，保存时转为兼容手机的MP4。竖图收窄完整展示；视频静音自动播放，播完下一条。编辑不选新文件则保留原媒体。</p>
        <label>推荐说明／适用条件<textarea required maxLength={1000} rows={4} value={editor.description}
          onChange={event => field('description', event.target.value)} /></label>
        <label>开始时间<input type="datetime-local" required value={editor.starts_at} onChange={event => field('starts_at', event.target.value)} /></label>
        <label>结束时间<input type="datetime-local" required value={editor.ends_at} onChange={event => field('ends_at', event.target.value)} /></label>
        <p className="admin-hint">按本设备时区填写，核对时显示完整时间；到期自动停止展示。</p>
        <label className="hotel-editor-visibility"><input type="checkbox" checked={editor.enabled} onChange={event => field('enabled', event.target.checked)} />
          <span>向客人展示<small>仅在有效期内展示；关闭后立即下架。</small></span></label>
      </fieldset></form> : <section><h3>核对推荐内容</h3><p>{recommendationKinds[pending.payload.kind]}：{pending.payload.title}</p>
        <p className="recommendation-admin-description">{pending.payload.description}</p>
        <p>{dateText(pending.payload.starts_at)} — {dateText(pending.payload.ends_at)}</p>
        <p>{isVideo ? '媒体：视频' : '媒体：照片'} · {pending.payload.enabled ? '确认后在有效期内公开展示' : '确认后下架推荐和媒体'}</p>
        <p className="admin-hint">点击确认表示已核实推荐真实、适用条件，以及人员成年／本人授权和照片使用许可。</p>
      </section>}
    </MusicDialog>}
  </section>
}
