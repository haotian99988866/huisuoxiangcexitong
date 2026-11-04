import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { API_BASE_URL } from '../utils/twa'
import { mediaUrl } from '../utils/tenant'
import MusicDialog from './MusicDialog'
import './DeliveryStaffAdmin.css'
import './StaffGallery.css'

function AttachmentMedia({ attachment }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [load, setLoad] = useState(attachment.media_type === 'photo')
  useEffect(() => {
    if (!load) return
    let active = true
    let objectUrl
    requestAdmin('admin_delivery_staff_attachment', { attachment_id: attachment.id }, {
      blob: true, timeoutMs: 60000, timeoutMessage: '媒体读取超时，请关闭档案后重试。',
    }).then(blob => {
      if (!active) return
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
    }).catch(failure => {
      console.error('[Delivery attachment]', failure)
      if (active) setError(failure.message)
    })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [attachment.id, load])
  if (error) return <p role="alert">{error}</p>
  if (!load) return <button type="button" onClick={() => setLoad(true)}>加载后台视频</button>
  if (!url) return <p role="status">正在加载媒体…</p>
  const onError = () => setError('媒体无法显示，请关闭档案后重试。')
  return attachment.media_type === 'video'
    ? <video controls playsInline preload="none" src={url} onError={onError} />
    : <img alt="后台补充照片" src={url} onError={onError} />
}

export default function DeliveryStaffAdmin({ venue, archive = false }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [selectedId, setSelectedId] = useState(null)
  const [description, setDescription] = useState('')
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState('')
  const [failedImages, setFailedImages] = useState(new Set())
  const [failedMedia, setFailedMedia] = useState(new Set())
  const writeLock = useRef(false)
  const fileInput = useRef(null)
  const people = data?.people.filter(person => !person.merged_into_id) || []
  const person = people.find(item => item.id === selectedId)
  const dirty = Boolean(file) || Boolean(person && description !== (person.description || ''))
  const clearFile = () => {
    setFile(null)
    if (fileInput.current) fileInput.current.value = ''
  }
  useEffect(() => {
    if (!file) { setPreview(''); return }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  useEffect(() => {
    let active = true
    setError('')
    requestAdmin('admin_delivery_staff', { venue_id: venue.id }).then(result => {
      if (active) setData(result)
    }).catch(failure => {
      console.error('[Delivery personnel]', failure)
      if (active) setError(failure.message)
    })
    return () => { active = false }
  }, [venue.id, attempt])

  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && !selectedId && !busy) {
        setAttempt(value => value + 1)
      }
    }, 60000)
    return () => clearInterval(timer)
  }, [selectedId, busy])

  const open = member => {
    setSelectedId(member.id)
    setDescription(member.description || '')
    setFailedMedia(new Set())
    setError('')
    setNotice('')
    clearFile()
  }
  const close = () => {
    if (writeLock.current) return
    if ((file || description !== (person?.description || '')) &&
        !window.confirm('还有未保存的说明或媒体，确认放弃并关闭？')) return
    setSelectedId(null)
    setError('')
    setNotice('')
    clearFile()
  }
  const chooseFile = event => {
    const chosen = event.target.files?.[0]
    clearFile()
    setError('')
    if (!chosen) return
    const photo = /\.(jpe?g|png|webp)$/i.test(chosen.name)
    const video = /\.(mp4|webm)$/i.test(chosen.name)
    if (!photo && !video) { setError('请选择 JPG、PNG、WebP 照片或 MP4、WebM 视频。'); return }
    if (!chosen.size || chosen.size > (photo ? 5 : 20) * 1024 * 1024) {
      setError('照片限5MB，视频限20MB，不能上传空文件。')
      return
    }
    setFile(chosen)
    setNotice('')
  }
  const save = async () => {
    if (writeLock.current || !person || !dirty) return
    const changes = [
      description !== (person.description || '') ? `档案说明：${description.trim() || '（清空）'}` : '',
      file ? `新增媒体：${file.name}` : '',
    ].filter(Boolean).join('\n')
    if (!window.confirm(`确认提交档案 #${person.id} 的修改？\n\n${changes}\n\n仅后台保存，不同步频道或客人端。`)) return
    writeLock.current = true
    setBusy('submit')
    setError('')
    setNotice('')
    try {
      if (!file) {
        const result = await requestAdmin('admin_delivery_staff_description', {
          person_id: person.id, description,
        }, { timeoutMessage: '说明保存超时，请刷新核对后再操作。' })
        setDescription(result.description)
        setData(current => ({ ...current, people: current.people.map(item =>
          item.id === person.id ? { ...item, description: result.description } : item) }))
        setNotice('修改已提交，档案说明已保存，仅后台可见。')
      } else {
        const result = await requestAdmin('admin_delivery_staff_upload', {
          person_id: person.id, description,
        }, {
          file, timeoutMs: 120000,
          timeoutMessage: '上传超时，结果尚未确认，请刷新核对；同一文件重传不会重复添加。',
        })
        const savedDescription = description.trim()
        setDescription(savedDescription)
        setData(current => ({ ...current, people: current.people.map(item =>
          item.id === person.id ? { ...item, description: savedDescription } : item) }))
        clearFile()
        setNotice(result.duplicate ? '修改已提交，说明已保存；媒体已存在，没有重复添加。' : '修改已提交，说明和媒体已保存，仅后台可见。')
        setAttempt(value => value + 1)
      }
    } catch (failure) {
      console.error('[Save delivery archive]', failure)
      setError(failure.message)
    } finally {
      writeLock.current = false
      setBusy('')
    }
  }

  const remove = async attachmentId => {
    if (writeLock.current || !person) return
    const message = attachmentId
      ? '确认永久删除这条后台补充媒体？频道原始媒体和客人端展示不受影响。'
      : `确认删除档案 #${person.id}？说明和后台补充媒体将永久删除，频道原始媒体和客人端展示保留。`
    if (!window.confirm(message)) return
    writeLock.current = true
    setBusy('delete')
    setError('')
    setNotice('')
    try {
      await requestAdmin(attachmentId ? 'admin_delivery_staff_attachment_delete' : 'admin_delivery_staff_delete', {
        person_id: person.id, ...(attachmentId ? { attachment_id: attachmentId } : {}), confirmed: true,
      }, { timeoutMessage: '删除结果尚未确认，请刷新核对，不要连续重复操作。' })
      if (attachmentId) {
        setData(current => ({ ...current, people: current.people.map(item => item.id === person.id
          ? { ...item, attachments: item.attachments.filter(media => media.id !== attachmentId) } : item) }))
        setNotice('后台补充媒体已删除。')
      } else {
        setData(current => ({ ...current, people: current.people.filter(item => item.id !== person.id) }))
        setSelectedId(null)
        clearFile()
        setDescription('')
        setNotice('后台档案已删除，频道及客人端原始展示保留。')
      }
    } catch (failure) {
      console.error('[Delete delivery archive]', failure)
      setError(failure.message)
    } finally {
      writeLock.current = false
      setBusy('')
    }
  }

  return <section className="staff-gallery delivery-staff-admin" aria-label={`${venue.name}外送人员管理`}>
    <div className="staff-heading">
      <h2>{archive ? '人员档案' : '人员概览'}</h2>
      <button type="button" disabled={Boolean(busy)} onClick={() => setAttempt(value => value + 1)}>刷新</button>
    </div>
    {archive && <p className="staff-meta">频道不同照片自动建档，重复照片不重复建档。手动添加的媒体补充到当前档案，说明和补充媒体仅后台可见。</p>}
    {error && !person && <p className="membership-error" role="alert">{error}</p>}
    {notice && !person && <p className="membership-notice" role="status">{notice}</p>}
    {!data && !error && <p role="status">正在读取人员档案…</p>}
    {data && <>
      <p className="staff-meta">全部人员 · {people.length} 份档案 · {data.shift_date} {data.shift}
        {data.shift === '晚班' && ` · 今晚出勤 ${data.on_duty_count} 位`}</p>
      {people.length === 0 && <p>频道收到照片或视频后会自动建立人员档案。</p>}
      <div className="staff-grid">
        {people.map(member => {
          const cover = member.media.find(media => media.media_type === 'photo')
          return <article className="staff-card" key={member.id}>
            <button type="button" className="staff-open" onClick={() => open(member)}>
              <div className="staff-cover">
                {cover && !failedImages.has(cover.id)
                  ? <img loading="lazy" src={mediaUrl(`${API_BASE_URL}/delivery-media/${cover.id}?thumb=1`)}
                    alt={`档案 #${member.id}`}
                    onError={() => setFailedImages(current => new Set([...current, cover.id]))} />
                  : <span>{cover ? '照片暂不可用' : '视频档案 · 点击查看'}</span>}
                <span className={`staff-attendance-overlay${member.on_duty ? ' is-on-duty' : ''}`}>
                  {member.attendance_overlay}
                </span>
              </div>
              <div className="staff-card-info">
                <strong>档案 #{member.id}</strong>
                <small>{member.media_count + (member.attachments?.length || 0)} 条照片/视频</small>
              </div>
            </button>
          </article>
        })}
      </div>
      {person && <MusicDialog className="account-sheet staff-sheet delivery-staff-detail"
        title={`${venue.name} · 档案 #${person.id}`} scrollBody onClose={close}
        headerContent={archive && <div className="delivery-staff-header-actions">
          <button type="button" className="delivery-staff-delete" disabled={Boolean(busy)}
            onClick={() => remove(null)}>{busy === 'delete' ? '删除中…' : '删除档案'}</button>
        </div>}
        footer={archive && <div className="delivery-staff-submit">
          <p role={error ? 'alert' : 'status'}>{busy === 'submit' ? '正在提交，请勿关闭…' :
            error || notice || (dirty ? '有未提交的修改' : '暂无未提交的修改')}</p>
          <div className="delivery-staff-submit-buttons">
            <button type="button" disabled={Boolean(busy)} onClick={close}>取消修改</button>
            <button type="button" className="delivery-staff-submit-primary" disabled={Boolean(busy) || !dirty}
              onClick={save}>{busy === 'submit' ? '提交中…' : '确认并提交'}</button>
          </div>
        </div>}>
        <div className="delivery-staff-detail-media">
          {person.media.map(media => <div key={media.id}>
            {failedMedia.has(media.id) ? <p role="alert">媒体加载失败，请关闭后重新打开档案。</p>
              : media.media_type === 'video'
                ? <video controls playsInline preload="none"
                  onError={() => setFailedMedia(current => new Set([...current, media.id]))}
                  src={mediaUrl(`${API_BASE_URL}/delivery-media/${media.id}`)} />
                : <img loading="lazy" alt={`档案 #${person.id}`}
                  onError={() => setFailedMedia(current => new Set([...current, media.id]))}
                  src={mediaUrl(`${API_BASE_URL}/delivery-media/${media.id}`)} />}
          </div>)}
          {person.attachments?.map(item => <div key={item.id}>
            <AttachmentMedia attachment={item} />
            {archive && <button type="button" className="delivery-staff-delete" disabled={Boolean(busy)}
              onClick={() => remove(item.id)}>{busy === 'delete' ? '删除中…' : '删除补充媒体'}</button>}
          </div>)}
        </div>
        <p className="staff-meta">{person.attendance_overlay} · {data.shift_date} {data.shift}。
          按频道本班照片记录匹配，未见照片不代表实际缺勤。</p>
        <p className="staff-meta">频道媒体 {person.media_count} 条 · 后台补充 {person.attachments?.length || 0} 条</p>
        <p className="staff-meta">频道最近更新：{person.latest_media_at
          ? new Date(person.latest_media_at).toLocaleString('zh-CN', { hour12: false }) : '暂无'}</p>
        {error && <p className="membership-error" role="alert">{error}</p>}
        {notice && <p className="membership-notice" role="status">{notice}</p>}
        {archive ? <>
          <label>档案说明
            <textarea maxLength={2000} rows={4} disabled={Boolean(busy)} value={description}
              onChange={event => setDescription(event.target.value)} placeholder="添加此档案的说明，仅后台可见" />
          </label>
          <label>添加媒体文件
            <input ref={fileInput} type="file" accept=".jpg,.jpeg,.png,.webp,.mp4,.webm"
              disabled={Boolean(busy)} onChange={chooseFile} />
          </label>
          <p className="staff-meta">每次添加一个文件：照片最多5MB，视频最多20MB。仅补充当前档案，不同步频道或客人端。</p>
          {file && <div className="delivery-staff-detail-media">
            <p>{file.name} · {(file.size / 1024 / 1024).toFixed(2)} MB</p>
            {preview && (/\.(mp4|webm)$/i.test(file.name)
              ? <video controls playsInline preload="none" src={preview} />
              : <img alt="待上传照片预览" src={preview} />)}
            <div className="delivery-staff-actions">
              <button type="button" disabled={Boolean(busy)} onClick={clearFile}>取消选择</button>
            </div>
          </div>}
        </> : person.description && <p className="delivery-staff-description">{person.description}</p>}
      </MusicDialog>}
    </>}
  </section>
}
