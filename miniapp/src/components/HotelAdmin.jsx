import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { saveHotel } from '../utils/hotels'
import { hotelDraft } from '../utils/hotelDraft'
import MusicDialog from './MusicDialog'
import './HotelAdmin.css'

const newId = () => crypto.randomUUID().replaceAll('-', '')
const draftKey = () => `hotel-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'telegram'}`

export default function HotelAdmin() {
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
  const writeLock = useRef(false)
  const form = useRef(null)
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    hotelDraft(draftKey(), 'get').then(saved => {
      if (!mounted.current || !saved) return
      if (!saved.payload || !/^[0-9a-f]{32}$/.test(saved.payload.id) ||
          !/^[0-9a-f]{32}$/.test(saved.payload.request_id) ||
          (saved.image !== null && !(saved.image instanceof Blob))) throw new Error('暂存酒店操作无效，请人工核对后再操作')
      setEditor(saved.payload); setFile(saved.image); setPending(saved); setUncertain(true)
    }).catch(failure => {
      console.error('[Restore hotel operation]', failure)
      if (mounted.current) setRecoveryError(`酒店暂存读取失败：${failure.message}，请先人工核对，不要重复新增。`)
    }).finally(() => { if (mounted.current) setRecovering(false) })
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    requestAdmin('admin_hotels', { page }).then(result => { if (active) setData(result) }).catch(failure => {
      console.error('[Admin hotels]', failure)
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
    requestAdmin('admin_hotel_image', { id: editor.id }).then(result => {
      if (active) setPreview(result.image)
    }).catch(failure => {
      console.error('[Hotel photo preview]', failure)
      if (active) setPreviewError(failure.message)
    })
    return () => { active = false }
  }, [editor?.id, file])
  const open = (hotel, enabled) => {
    if (busy || recovering || recoveryError || uncertain) return
    setError(''); setNotice(''); setFile(null); setPending(null)
    setEditor(hotel ? { ...hotel, enabled: enabled ?? hotel.enabled } :
      { id: newId(), revision: 0, name: '', map_url: '', telegram_username: '', enabled: true })
  }
  const close = () => {
    if (writeLock.current || uncertain) return
    if (pending) { setPending(null); return }
    setEditor(null); setFile(null)
  }
  const prepare = event => {
    event.preventDefault()
    setError('')
    if (!editor.revision && !file) { setError('新增酒店请上传门头照'); return }
    setPending({ payload: {
      id: editor.id, revision: editor.revision, name: editor.name.trim(), map_url: editor.map_url.trim(),
      telegram_username: editor.telegram_username.trim().replace(/^@/, ''), enabled: editor.enabled,
      request_id: newId(), confirmed: true,
    }, image: file })
  }
  const save = async () => {
    if (writeLock.current || !pending) return
    writeLock.current = true; setBusy(true); setError('')
    let sent = false
    try {
      await hotelDraft(draftKey(), 'put', pending)
      sent = true
      await saveHotel(pending.payload, pending.image)
      await hotelDraft(draftKey(), 'delete')
      if (mounted.current) {
        setEditor(null); setFile(null); setPending(null); setUncertain(false)
        setNotice(pending.payload.enabled ? '酒店资料已保存并展示。' : '酒店已下架，客人列表和照片入口不再展示。')
        setReload(value => value + 1)
      }
    } catch (failure) {
      console.error('[Save hotel]', failure)
      if (!mounted.current) return
      setError(failure.message)
      if ([400, 401, 403, 409, 413].includes(failure.status)) {
        try {
          await hotelDraft(draftKey(), 'delete')
          setPending(null); setUncertain(false)
          if ([401, 403, 409].includes(failure.status)) { setEditor(null); setReload(value => value + 1) }
        } catch (storageFailure) {
          console.error('[Clear hotel pending]', storageFailure)
          setUncertain(true); setError(`暂存清理失败：${storageFailure.message}；请先核对保存结果`)
        }
      } else if (sent) setUncertain(true)
    } finally {
      writeLock.current = false
      if (mounted.current) setBusy(false)
    }
  }
  const disabled = busy || recovering || Boolean(recoveryError) || Boolean(editor)
  return <section className="admin-card hotel-admin" aria-label="酒店管理">
    <div className="hotel-admin-heading">
      <div><span className="hotel-admin-eyebrow">HOTEL DIRECTORY</span><h2>酒店推荐管理</h2></div>
      <button className="hotel-admin-add" type="button" disabled={disabled} onClick={() => open(null)}>＋ 新增酒店</button>
    </div>
    <p className="hotel-admin-intro">维护酒店门头照片、地图位置与前台联系方式。</p>
    <p className="hotel-admin-guidance">请确认资料真实，并已获门头照使用许可后再发布。</p>
    {recoveryError && <p className="admin-error" role="alert">{recoveryError}</p>}
    {!editor && error && <p className="admin-error" role="alert">{error}</p>}
    {notice && <p className="admin-notice" role="status">{notice}</p>}
    <div className="hotel-admin-toolbar">
      <span>{data ? `共 ${data.total} 家酒店` : '酒店目录'}</span>
      <button type="button" disabled={loading || disabled} onClick={() => setReload(value => value + 1)}>
        <span aria-hidden="true">↻</span> 刷新
      </button>
    </div>
    {loading && <p className="hotel-admin-loading" role="status">正在读取酒店资料…</p>}
    {data && <>
      {!data.hotels.length && <div className="hotel-admin-empty"><span aria-hidden="true">⌂</span><strong>还没有酒店资料</strong>
        <p>添加酒店门头照、地图链接和前台联系方式后即可开始管理。</p></div>}
      <div className="hotel-admin-list">{data.hotels.map(hotel => <article className="hotel-admin-item" key={hotel.id}>
        <div className="hotel-admin-item-heading"><div><h3>{hotel.name}</h3>
          <span className={`hotel-admin-status${hotel.enabled ? ' is-active' : ''}`}>
            <i aria-hidden="true" />{hotel.enabled ? '展示中' : '已下架'}
          </span></div></div>
        <a className="hotel-admin-contact" href={`https://t.me/${hotel.telegram_username}`}
          target="_blank" rel="noopener noreferrer"><span aria-hidden="true">@</span>
          <span><small>前台 Telegram</small><strong>{hotel.telegram_username}</strong></span></a>
        <a className="hotel-admin-map" href={hotel.map_url} target="_blank" rel="noopener noreferrer">
          <span aria-hidden="true">⌖</span> 核对地图位置 <span aria-hidden="true">↗</span>
        </a>
        <div className="hotel-admin-actions">
          <button type="button" disabled={disabled} onClick={() => open(hotel)}>编辑资料</button>
          <button className={hotel.enabled ? 'hotel-admin-unpublish' : 'hotel-admin-publish'}
            type="button" disabled={disabled} onClick={() => open(hotel, !hotel.enabled)}>
            {hotel.enabled ? '下架酒店' : '重新上架'}</button>
        </div>
      </article>)}</div>
      {data.total > 20 && <nav className="hotel-admin-actions" aria-label="酒店管理分页">
        <button type="button" disabled={disabled || page === 1} onClick={() => setPage(value => value - 1)}>上一页</button>
        <span>{page} / {Math.ceil(data.total / 20)}</span>
        <button type="button" disabled={disabled || page * 20 >= data.total} onClick={() => setPage(value => value + 1)}>下一页</button>
      </nav>}
    </>}
    {editor && <MusicDialog className="hotel-editor-sheet" title={editor.revision ? '编辑酒店推荐' : '新增酒店推荐'} scrollBody onClose={close}
      footer={<div className="hotel-admin-actions">
        <button type="button" disabled={busy || uncertain} onClick={close}>{pending ? '返回修改' : '取消'}</button>
        <button type="button" className="hotel-admin-primary" disabled={busy} onClick={() => pending ? save() : form.current?.requestSubmit()}>
          {busy ? '保存中…' : uncertain ? '原编号重试' : pending ? '确认保存' : '下一步：核对'}</button>
      </div>}>
      {error && <p className="admin-error" role="alert">{error}</p>}
      {uncertain && <p className="admin-hint">上次保存结果未确认，资料和照片已暂存；请用原编号重试，不要重复新增。</p>}
      {preview && <img className="hotel-admin-preview" src={preview} alt="酒店门头照片预览" onError={() => {
        setPreview(''); setPreviewError('门头照片预览失败，请重新选择有效照片')
      }} />}
      {previewError && <p className="admin-error" role="alert">{previewError}</p>}
      {!pending ? <form ref={form} className="hotel-editor-form" onSubmit={prepare}>
        <fieldset disabled={busy}>
          <label>酒店名称<input required maxLength={100} value={editor.name} onChange={e => setEditor(current => ({ ...current, name: e.target.value }))} /></label>
          <label>门头照片<input type="file" accept="image/jpeg,image/png,image/webp" required={!editor.revision}
            onChange={event => {
              const selected = event.target.files?.[0]
              if (selected && selected.size > 5 * 1024 * 1024) {
                setError('门头照片不能超过5MB'); event.target.value = ''; setFile(null); return
              }
              setError(''); setFile(selected || null)
            }} /></label>
          <p className="admin-hint">JPG、PNG或WEBP，最多5MB、2000万像素。编辑时不选新照片则保留原图。</p>
          <label>地图链接<input required type="url" maxLength={2048} placeholder="https://maps.app.goo.gl/..."
            value={editor.map_url} onChange={e => setEditor(current => ({ ...current, map_url: e.target.value }))} /></label>
          <label>前台Telegram用户名<input required maxLength={33} pattern="@?[A-Za-z][A-Za-z0-9_]{4,31}" placeholder="@hotel_frontdesk"
            value={editor.telegram_username} onChange={e => setEditor(current => ({ ...current, telegram_username: e.target.value }))} /></label>
          <label className="hotel-editor-visibility"><input type="checkbox" checked={editor.enabled}
            onChange={e => setEditor(current => ({ ...current, enabled: e.target.checked }))} />
            <span>向客人展示<small>关闭后下架，客人无法查看此酒店。</small></span></label>
        </fieldset>
      </form> : <section>
        <h3>核对酒店资料</h3><p>酒店：{pending.payload.name}</p>
        <p className="hotel-admin-link">地图：{pending.payload.map_url}</p>
        <p>前台：@{pending.payload.telegram_username}</p>
        <p>{pending.payload.enabled ? '确认后在酒店推荐列表展示' : '确认后下架，客人无法查看此酒店和门头照'}</p>
      </section>}
    </MusicDialog>}
  </section>
}
