import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import MusicIcon from '../components/MusicIcon'
import MusicDialog from '../components/MusicDialog'
import { subscribeFullscreen } from '../utils/twa'
import './FinancePreview.css'

const tabs = [['overview', '总览'], ['ledger', '账单'], ['assets', '资产'], ['analysis', '分析']]
const entries = [
  { id: 1, title: '客人提成', note: '今天 20:30 · 消费自动同步', amount: 40, kind: 'income' },
  { id: 2, title: '餐饮', note: '今天 12:15 · 日常账户', amount: -12, kind: 'expense' },
  { id: 3, title: '客人提成', note: '昨天 19:20 · 消费自动同步', amount: 120, kind: 'income' },
]
const amount = value => `$${Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function FinancePreview() {
  const [tab, setTab] = useState('overview')
  const [visible, setVisible] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const [period, setPeriod] = useState('month')
  const [editor, setEditor] = useState(null)
  const [message, setMessage] = useState('')
  useEffect(() => subscribeFullscreen(setFullscreen), [])
  const display = value => tab === 'overview' && !visible ? '$••••' : amount(value)
  const selectTab = value => {
    setTab(value)
    setMessage('')
    window.scrollTo({ top: 0, behavior: 'instant' })
  }
  const rows = period === 'today' ? entries.slice(0, 2) : entries
  const summary = <div className="fp-summary">
    {[['收入', period === 'today' && tab === 'ledger' ? 40 : 160, 'income'],
      ['支出', 12, 'expense'], ['结余', period === 'today' && tab === 'ledger' ? 28 : 148, '']].map(([label, value, tone]) =>
      <div key={label}><span>{label}</span><strong className={tone}>{display(value)}</strong></div>)}
  </div>
  const ledger = list => <div className="fp-ledger">
    {list.map(row => <div className="fp-entry" key={row.id}>
      <span className={`fp-entry-icon ${row.kind}`} aria-hidden="true">{row.amount > 0 ? '↙' : '↗'}</span>
      <div><strong>{row.title}</strong><small>{row.note}</small></div>
      <strong className={row.kind}>{tab === 'overview' && !visible ? '$••••' : `${row.amount > 0 ? '+' : '−'}${amount(row.amount)}`}</strong>
    </div>)}
  </div>

  return <main className={`fp-page${fullscreen ? ' fp-fullscreen' : ''}`} aria-label="阿兄账房设计预览">
    <div className="fp-preview-label"><span>设计预览 · 全部为模拟数据</span><Link to="/personal-finance">退出预览</Link></div>
    <header className="fp-header"><div><span>PERSONAL FINANCE</span><h1>阿兄账房</h1></div>
      <span className="fp-header-note">把每一笔，记清楚</span></header>

    {tab === 'overview' && <>
      <section className="fp-hero" aria-label="资产总览">
        <div className="fp-hero-label"><span>个人净资产 · USD</span>
          <button type="button" aria-label={visible ? '隐藏所有总览金额' : '显示所有总览金额'}
            aria-pressed={visible} onClick={() => setVisible(value => !value)}>
            <MusicIcon name={visible ? 'eye' : 'eyeOff'} />
          </button></div>
        <button type="button" className="fp-total" aria-label="切换总览金额显示" onClick={() => setVisible(value => !value)}>
          {display(434.41)}
        </button>
        <div className="fp-hero-details">
          {[['账户余额', 434.41], ['待收借款', 200], ['待还借款', 200]].map(([label, value]) =>
            <div key={label}><span>{label}</span><strong>{display(value)}</strong></div>)}
        </div>
      </section>
      <section className="fp-section"><div className="fp-heading"><h2>本月收支</h2>
        <button type="button" onClick={() => { setPeriod('month'); selectTab('ledger') }}>示例月份 ›</button></div>
        {summary}
        <p className="fp-footnote">收入含自动同步提成，借还本金不计收支</p>
      </section>
      <section className="fp-section"><div className="fp-heading"><h2>待处理</h2><span>1 笔</span></div>
        <button type="button" className="fp-reminder" onClick={() => selectTab('assets')}>
          <span className="fp-reminder-icon" aria-hidden="true">↗</span>
          <span><strong>借款待归还</strong><small>示例约定：本周五 · 日常账户</small></span>
          <strong>{display(200)} ›</strong>
        </button>
      </section>
      <section className="fp-section"><div className="fp-heading"><h2>最近流水</h2>
        <button type="button" onClick={() => { setPeriod('month'); selectTab('ledger') }}>全部 ›</button></div>{ledger(entries)}</section>
    </>}

    {tab === 'ledger' && <>
      <div className="fp-page-title"><h2>收支账单</h2><span>示例账期</span></div>
      <div className="fp-segment" aria-label="账单时间范围">{[['month', '本月'], ['today', '今日']].map(([key, label]) =>
        <button type="button" key={key} aria-pressed={period === key} onClick={() => setPeriod(key)}>{label}</button>)}</div>
      <section className="fp-section">{summary}</section>
      <section className="fp-section"><div className="fp-heading"><h2>{period === 'today' ? '今日流水' : '本月流水'}</h2>
        <span>{rows.length} 笔</span></div>{ledger(rows)}</section>
    </>}

    {tab === 'assets' && <>
      <div className="fp-page-title"><h2>资产与往来</h2><span>当前余额</span></div>
      <section className="fp-section"><div className="fp-heading"><h2>我的账户</h2>
        <button type="button" onClick={() => setEditor('账户设置')}>设置 ›</button></div>
        {[['日常账户', 148], ['储蓄账户', 286.41]].map(([label, value]) =>
          <div className="fp-asset-row" key={label}><span>{label}</span><strong>{display(value)}</strong></div>)}
        <button type="button" className="fp-outline" onClick={() => setEditor('账户转账')}>账户转账</button>
      </section>
      <section className="fp-section"><div className="fp-heading"><h2>借款往来</h2><span>本金不计收支</span></div>
        <div className="fp-asset-row"><div><strong>待还借款</strong><small>示例对象 A · 本周五</small></div>
          <strong>{display(200)}</strong><button type="button" onClick={() => setEditor('偿还借款')}>还款</button></div>
        <div className="fp-asset-row"><div><strong>待收借款</strong><small>示例对象 B · 未设日期</small></div>
          <strong>{display(200)}</strong><button type="button" onClick={() => setEditor('收回借款')}>收款</button></div>
      </section>
    </>}

    {tab === 'analysis' && <>
      <div className="fp-page-title"><h2>收支分析</h2><span>示例月份</span></div>
      <section className="fp-section"><div className="fp-heading"><h2>钱从哪里来</h2><strong className="income">{display(160)}</strong></div>
        <div className="fp-analysis-row"><span>客人提成</span><strong>100%</strong></div>
        <progress max="100" value="100" aria-label="客人提成占收入100%" />
        <p className="fp-footnote">自动同步收入，不需要重复记账</p>
      </section>
      <section className="fp-section"><div className="fp-heading"><h2>钱花在哪里</h2><strong className="expense">{display(12)}</strong></div>
        <div className="fp-analysis-row"><span>餐饮</span><strong>100%</strong></div>
        <progress max="100" value="100" aria-label="餐饮占支出100%" />
      </section>
      <section className="fp-section"><div className="fp-heading"><h2>月度预算</h2>
        <button type="button" onClick={() => setEditor('预算设置')}>调整 ›</button></div>
        <div className="fp-analysis-row"><span>餐饮 · 已用 {display(12)}</span><strong>预算 {display(100)}</strong></div>
        <progress max="100" value="12" aria-label="餐饮预算已用12%" />
        <p className="fp-footnote">剩余 {display(88)} · 当前在预算内</p>
      </section>
    </>}

    {message && <p className="fp-message" role="status">{message}</p>}
    <footer className="fp-dock">
      <button type="button" className="fp-record" onClick={() => setEditor('记一笔')}><MusicIcon name="plus" />记一笔</button>
      <nav aria-label="账房导航">{tabs.map(([key, label]) =>
        <button key={key} type="button" aria-current={tab === key ? 'page' : undefined} onClick={() => selectTab(key)}>{label}</button>)}</nav>
    </footer>

    {editor && <MusicDialog title={`${editor} · 预览`} className="fp-dialog" scrollBody onClose={() => setEditor(null)}>
      <p className="fp-footnote">这里只体验布局，不连接真实账本，也不会保存或转账。</p>
      <form className="fp-demo-form" onSubmit={event => {
        event.preventDefault()
        setMessage(`${editor}体验完成，模拟内容未保存，真实账目未改变。`)
        setEditor(null)
      }}>
        <label>{editor === '账户设置' ? '账户名称' : '项目说明'}<input required defaultValue={editor === '账户设置' ? '日常账户' : ''} placeholder="输入示例内容" /></label>
        <label>金额 USD<input required inputMode="decimal" pattern="\d{1,10}(\.\d{1,2})?" placeholder="例如 12.00" /></label>
        <button type="submit">完成体验（不保存）</button>
      </form>
    </MusicDialog>}
  </main>
}
