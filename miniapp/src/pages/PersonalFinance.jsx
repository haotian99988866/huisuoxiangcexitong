import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import FinanceAdmin from '../components/FinanceAdmin'
import { subscribeFullscreen } from '../utils/twa'
import './Admin.css'

const ENTRY_KINDS = new Set(['expense', 'income', 'transfer'])
const VIEWS = {
  today: 'today',
  month: 'records',
  analysis: 'analysis',
  overview: 'overview',
  assets: 'assets',
  settings: 'settings',
  planning: 'planning',
}

export default function PersonalFinance() {
  const [fullscreen, setFullscreen] = useState(false)
  useEffect(() => subscribeFullscreen(setFullscreen), [])
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const fromAdmin = searchParams.get('from') === 'admin'
  const returnToAdmin = useCallback(() => navigate('/admin', { replace: true }), [navigate])
  const requestedView = searchParams.get('view')
  const requestedKind = searchParams.get('kind')
  const initialEntryKind = requestedView === 'entry' && ENTRY_KINDS.has(requestedKind)
    ? requestedKind : null
  const initialView = VIEWS[requestedView] || 'overview'

  return <main className={`admin-console personal-finance-page fp-page${fullscreen ? ' personal-finance-fullscreen' : ''}`} aria-label="个人财务">
    <FinanceAdmin
      key={`${requestedView || 'overview'}:${initialEntryKind || ''}`}
      initialView={initialView}
      initialEntryKind={initialEntryKind}
      onExit={fromAdmin ? returnToAdmin : null}
    />
  </main>
}
