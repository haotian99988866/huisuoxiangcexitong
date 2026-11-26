import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { consumptionDate } from '../utils/customerRecords'
import { API_BASE_URL } from '../utils/twa'
import { telegramDownload } from '../utils/priceDownload'
import { importedPrices, priceAmount as amount, priceSheetRows } from '../utils/priceSpreadsheet'
import './PriceAdmin.css'

const rowKey = row => `${row.venue_id}:${row.service_key}`
const shown = value => value == null ? '待确认' : `$${value}`

export default function PriceAdmin() {
  const [data, setData] = useState(null)
  const [drafts, setDrafts] = useState({})
  const [venue, setVenue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [needsRefresh, setNeedsRefresh] = useState(false)
  const mounted = useRef(true)
  const requestId = useRef(0)
  const fileInput = useRef(null)

  const load = async () => {
    const id = ++requestId.current
    setBusy(true)
    setError('')
    try {
      const result = await requestAdmin('admin_price_overview')
      if (!mounted.current || id !== requestId.current) return
      setData(result)
      setDrafts(Object.fromEntries(result.items.map(item => [rowKey(item), {
        price: item.price ?? '', commission: item.commission_amount,
      }])))
      setVenue(current => result.items.some(item => String(item.venue_id) === current)
        ? current : String(result.items[0]?.venue_id || ''))
      setNeedsRefresh(false)
      setConfirming(false)
    } catch (failure) {
      if (mounted.current && id === requestId.current) setError(failure.message)
    } finally {
      if (mounted.current && id === requestId.current) setBusy(false)
    }
  }
  useEffect(() => {
    mounted.current = true
    load()
    return () => { mounted.current = false; requestId.current += 1 }
  }, [])

  const entries = data?.items || []
  const groups = [...new Map(entries.map(item => [item.venue_id, item.venue_name])).entries()]
  const changes = entries.flatMap(item => {
    const draft = drafts[rowKey(item)]
    if (!draft) return []
    const priceChanged = draft.price === '' ? item.price !== null
      : amount(draft.price) !== amount(item.price || '')
    const commissionChanged = amount(draft.commission) !== amount(item.commission_amount)
    return priceChanged || commissionChanged ? [{ item, draft, priceChanged, commissionChanged }] : []
  })
  const update = (item, field, value) => {
    setDrafts(current => ({ ...current, [rowKey(item)]: { ...current[rowKey(item)], [field]: value } }))
    setNotice('')
  }
  const preview = () => {
    const invalid = changes.find(({ item, draft, priceChanged, commissionChanged }) =>
      (priceChanged && (amount(draft.price) === null || amount(draft.price) <= 0)) ||
      (commissionChanged && amount(draft.commission) === null) ||
      (item.price === null && draft.price === '' && priceChanged))
    if (invalid) {
      setError(`${invalid.item.service_name}：价格须大于零，提成不能为负，金额最多两位小数。`)
      return
    }
    setError('')
    setConfirming(true)
  }
  const save = async () => {
    if (!data || busy || needsRefresh) return
    setBusy(true)
    setError('')
    try {
      const result = await requestAdmin('admin_price_save', {
        revision: data.revision,
        confirmed: true,
        changes: changes.map(({ item, draft, priceChanged, commissionChanged }) => ({
          venue_id: item.venue_id, service_key: item.service_key,
          old_price: item.price, old_commission: item.commission_amount,
          ...(priceChanged ? { price: draft.price } : {}),
          ...(commissionChanged ? { commission: draft.commission } : {}),
        })),
      })
      if (!mounted.current) return
      setNotice(result.channel_sync_venues.length
        ? '已保存，频道价格等待同步；外送频道不修改。'
        : '已保存。已有消费的提成不变。')
      setNeedsRefresh(true)
      await load()
    } catch (failure) {
      if (mounted.current) {
        setError(`${failure.message}。请刷新核对实际结果后再操作。`)
        setNeedsRefresh(true)
        setConfirming(false)
      }
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const retrySync = async task => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await requestAdmin('admin_price_sync_retry', { revision: task.revision, venue_id: task.venue_id })
      if (mounted.current) {
        setNotice('已重新安排频道同步。')
        await load()
      }
    } catch (failure) {
      if (mounted.current) setError(`${failure.message}。请刷新后查看同步状态。`)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const exportPrices = async () => {
    setBusy(true)
    setError('')
    try {
      const { default: writeExcelFile } = await import('write-excel-file/browser')
      const name = groups.find(([id]) => String(id) === venue)?.[1] || '价目'
      const rows = priceSheetRows(entries.filter(item => String(item.venue_id) === venue))
      const fileName = `${name.replace(/[\\/:*?"<>|]/g, '_')}-价目表.xlsx`
      const file = writeExcelFile(rows, { columns: [{ width: 35 }, { width: 18 }, { width: 14 }, { width: 24 }] })
      const tg = window.Telegram?.WebApp
      if (tg?.initData) {
        const bytes = new Uint8Array(await (await file.toBlob()).arrayBuffer())
        if (bytes.length > 8192) throw new Error('价目表文件过大，请联系管理员')
        const content = btoa(String.fromCharCode(...bytes))
        const { token } = await requestAdmin('admin_price_download', { content })
        const url = new URL(`${API_BASE_URL}/price-download/${token}`, window.location.origin).href
        await telegramDownload(tg, url, fileName)
        if (mounted.current) setNotice('已请求下载，请在 Telegram 下载记录中查看。')
      } else {
        await file.toFile(fileName)
        if (mounted.current) setNotice('已请求下载，请在浏览器下载记录中查看。')
      }
    } catch (failure) {
      if (mounted.current) setError(`导出失败：${failure.message}`)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const importPrices = async event => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setError('')
    setNotice('')
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 1024 * 1024) {
      setError('请选择不超过 1 MB 的 .xlsx 表格。')
      return
    }
    setBusy(true)
    try {
      const { readSheet } = await import('read-excel-file/browser')
      const prices = importedPrices(await readSheet(file), entries, venue)
      const overwrites = Object.entries(prices).some(([key, price]) => {
        const item = entries.find(entry => rowKey(entry) === key)
        return drafts[key].price !== (item.price ?? '') && drafts[key].price !== price
      })
      if (overwrites && !window.confirm('导入会覆盖当前会所未保存的价格修改，是否继续？')) return
      setDrafts(current => {
        const next = { ...current }
        for (const [key, price] of Object.entries(prices)) {
          next[key] = { ...current[key], price }
        }
        return next
      })
      setNotice('价格已导入，请核对修改并确认保存。个人提成不受影响。')
    } catch (failure) {
      if (mounted.current) setError(`导入失败：${failure.message}`)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  return <section className="price-admin">
    <div className="admin-header"><h2>价目与提成</h2>
      <button type="button" disabled={busy} onClick={() => {
        if (changes.length && !needsRefresh && !window.confirm('未保存的修改将被清除，确认刷新？')) return
        setNotice('')
        load()
      }}>刷新</button>
    </div>
    <p className="admin-hint">价格用于展示；提成为当前管理员个人设置。历史消费提成不变，KTV 不在此修改。</p>
    {notice && <p className="admin-notice" role="status">{notice}</p>}
    {error && <p className="admin-error" role="alert">{error}
      {needsRefresh && <button type="button" disabled={busy} onClick={load}>刷新核对</button>}</p>}
    {busy && !data && <p role="status">正在读取价目…</p>}
    {!data && !busy && <button type="button" onClick={load}>重新读取</button>}
    {data && <>
      {confirming ? <section className="price-admin-confirm">
        <h3>确认修改（{changes.length} 项）</h3>
        {changes.map(({ item, draft, priceChanged, commissionChanged }) =>
          <div className="price-admin-change" key={rowKey(item)}>
            <strong>{item.venue_name} · {item.service_name}</strong>
            {priceChanged && <span>价格：{shown(item.price)} → ${draft.price}</span>}
            {commissionChanged && <span>个人提成：{shown(item.commission_amount)} → ${draft.commission}</span>}
          </div>)}
        <p className="admin-hint">会所价格变更后同步到频道；外送频道不修改。</p>
        <div className="price-admin-actions">
          <button type="button" disabled={busy} onClick={() => setConfirming(false)}>返回修改</button>
          <button type="button" disabled={busy || needsRefresh} onClick={save}>{busy ? '正在保存…' : '确认保存'}</button>
        </div>
      </section> : <>
        <label className="price-admin-venue">选择会所或外送
          <select value={venue} disabled={busy || needsRefresh} onChange={event => setVenue(event.target.value)}>
            {groups.map(([id, name]) => <option value={id} key={id}>{name}</option>)}
          </select>
        </label>
        <div className="price-admin-actions">
          <button type="button" disabled={busy || needsRefresh || !venue} onClick={exportPrices}>导出 Excel</button>
          <button type="button" disabled={busy || needsRefresh || !venue}
            onClick={() => fileInput.current?.click()}>导入 Excel</button>
          <input ref={fileInput} type="file" accept=".xlsx" hidden onChange={importPrices} />
        </div>
        <p className="admin-hint">导出已保存价格；导入后需确认保存。空价格不修改，请勿更改项目及编号。</p>
        <div className="price-admin-list">
          {entries.filter(item => String(item.venue_id) === venue).map(item => {
            const draft = drafts[rowKey(item)]
            if (!draft) return null
            return <article className="price-admin-row" key={rowKey(item)}>
              <strong>{item.service_name}</strong>
              <div className="price-admin-fields">
                <label>价格（美元）
                  <input inputMode="decimal" value={draft.price} placeholder="待确认" disabled={busy || needsRefresh}
                    onChange={event => update(item, 'price', event.target.value)} />
                </label>
                <label>个人提成（美元）
                  <input inputMode="decimal" value={draft.commission} disabled={busy || needsRefresh}
                    onChange={event => update(item, 'commission', event.target.value)} />
                </label>
              </div>
            </article>
          })}
        </div>
        <button type="button" className="price-admin-preview" disabled={busy || needsRefresh || !changes.length}
          onClick={preview}>核对修改{changes.length ? `（${changes.length}）` : ''}</button>
      </>}
      {data.channel_sync.length > 0 && <details className="price-admin-reviews">
        <summary>频道同步（最近 {data.channel_sync.length} 条）</summary>
        {data.channel_sync.map(task => <p className="admin-hint" key={`${task.revision}:${task.venue_id}`}>
          {groups.find(([id]) => id === task.venue_id)?.[1] || `来源 ${task.venue_id}`} ·
          {consumptionDate(task.created_at)} · {task.reason}
          {task.status === 'failed' && <button type="button" disabled={busy}
            onClick={() => retrySync(task)}>重新同步</button>}
        </p>)}
      </details>}
    </>}
  </section>
}
