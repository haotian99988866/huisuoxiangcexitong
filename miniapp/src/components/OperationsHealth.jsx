import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import './OperationsHealth.css'

const refreshInterval = 30000
const stateLabels = {
  active: '心跳正常',
  stale: '心跳超时',
  not_started: '尚未启动',
  not_initialized: '尚无状态',
  disabled: '已停用',
  unavailable: '状态不可用',
}
const statusLabels = {
  queued: '待处理',
  sending: '发送中',
  forwarded: '已转发待入库',
  sent: '已完成',
  skipped: '已跳过',
  excluded: '已排除',
  failed: '失败',
  uncertain: '结果待核对',
  preparing: '准备中',
  ready: '已准备',
  pending_index: '公告待入库',
}
const problemStatuses = new Set(['failed', 'uncertain', 'pending_index'])

function formatTime(value) {
  if (!value) return '暂无记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间不可用' : date.toLocaleString('zh-CN', { hour12: false })
}

function ComponentCard({ component }) {
  const hasProblem = Object.entries(component.counts).some(([status, count]) =>
    problemStatuses.has(status) && count > 0)
  const state = hasProblem && component.state === 'active' ? 'attention' : component.state
  const label = state === 'attention' ? '队列需核查' : stateLabels[state] || '状态未知'
  return <article className={`operations-health-card is-${state}`}>
    <header>
      <h3>{component.name}</h3>
      <span className="operations-health-badge">{label}</span>
    </header>
    <p className="operations-health-time">
      最近心跳：{formatTime(component.last_heartbeat)}
      {component.heartbeat_age_seconds !== null && ` · ${component.heartbeat_age_seconds} 秒前`}
    </p>
    {component.note && <p className="operations-health-note">{component.note}</p>}
    {component.last_error_code && <p className="operations-health-note" role="status">
      最近错误：{component.last_error_code} · {formatTime(component.last_error_at)}
    </p>}
    {Object.keys(component.counts).length > 0
      ? <dl className="operations-health-counts">
        {Object.entries(component.counts).map(([status, count]) =>
          <div className={problemStatuses.has(status) && count ? 'has-problem' : ''} key={status}>
            <dt>{statusLabels[status] || status}</dt><dd>{count}</dd>
          </div>)}
      </dl>
      : <p className="operations-health-note">暂无队列记录</p>}
  </article>
}

export default function OperationsHealth() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshKey, setRefreshKey] = useState(0)
  const mounted = useRef(false)
  const inFlight = useRef(false)

  useEffect(() => {
    mounted.current = true
    let timer
    const refresh = async () => {
      if (inFlight.current) return
      inFlight.current = true
      if (mounted.current) setLoading(true)
      try {
        const result = await requestAdmin('admin_operations_health')
        if (mounted.current) {
          setData(result)
          setError('')
        }
      } catch (failure) {
        if (mounted.current) setError(failure.message)
      } finally {
        inFlight.current = false
        if (mounted.current) {
          setLoading(false)
          timer = window.setTimeout(refresh, refreshInterval)
        }
      }
    }
    refresh()
    return () => {
      mounted.current = false
      window.clearTimeout(timer)
    }
  }, [refreshKey])

  return <section className="admin-card operations-health" aria-label="运营健康面板">
    <header className="operations-health-header">
      <div><h2>运营健康</h2><p>只读监测同步任务与待处理队列，每30秒自动刷新。</p></div>
      <button type="button" disabled={loading} onClick={() => setRefreshKey(value => value + 1)}>
        {loading ? '读取中…' : '重新读取'}
      </button>
    </header>
    {error && <p className="admin-error" role="alert">{error}</p>}
    {loading && !data && <p role="status">正在读取任务状态…</p>}
    {data && <>
      <p className="operations-health-checked">检查时间：{formatTime(data.checked_at)}</p>
      <div className="operations-health-grid">
        {data.components.map(component => <ComponentCard key={component.key} component={component} />)}
      </div>
      <p className="operations-health-footnote">
        心跳超过 {data.stale_after_seconds} 秒标记为超时。失败或结果不明只作提示，不会自动重试或重新发布。
      </p>
    </>}
  </section>
}
