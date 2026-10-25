import { useNavigate } from 'react-router-dom'

const tabs = [
  { key: 'venue', label: '🏠 会所/外送', path: '/' },
  { key: 'movies', label: '🎬 观影', path: '/movies' },
  { key: 'recharge', label: '💳 充值', path: '/recharge' }
]

export default function ModuleTabs({ active }) {
  const navigate = useNavigate()
  return (
    <div
      role="tablist"
      style={{
        display: 'flex',
        gap: '4px',
        padding: '3px',
        marginBottom: '8px',
        background: 'rgba(160, 122, 69, 0.12)',
        borderRadius: '12px'
      }}
    >
      {tabs.map(tab => {
        const selected = tab.key === active
        return (
          <button
            key={tab.key}
            role="tab"
            aria-selected={selected}
            onClick={() => !selected && navigate(tab.path, { replace: true })}
            style={{
              flex: 1,
              height: '34px',
              border: 0,
              borderRadius: '10px',
              fontSize: '14px',
              fontWeight: 700,
              cursor: 'pointer',
              color: selected ? '#fff' : '#7a5c35',
              background: selected ? 'linear-gradient(135deg, #b88a4a, #9a6f35)' : 'transparent',
              boxShadow: selected ? '0 2px 6px rgba(120, 90, 50, 0.25)' : 'none'
            }}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}
