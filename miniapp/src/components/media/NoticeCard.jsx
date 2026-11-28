import ModuleIcon from '../ModuleIcon'
import ContactButton from './ContactButton'

export default function NoticeCard({ icon, title, updatedAt, contactOnly = false, children }) {
  return (
    <div style={{
      marginBottom: '24px',
      padding: '28px 18px',
      textAlign: 'center',
      background: 'var(--home-surface)',
      border: '1px solid var(--ink-line)',
      borderRadius: '14px',
      color: 'var(--ink-accent)',
      boxShadow: '0 3px 14px rgba(44, 65, 48, 0.03)'
    }}>
      <div className="home-notice-icon"><ModuleIcon name={icon} /></div>
      <div style={{ fontSize: '16px', fontWeight: 700, marginBottom: '8px' }}>{title}</div>
      <p style={{ margin: '0 0 16px', fontSize: '13px', lineHeight: 1.7, color: 'var(--ink-muted)' }}>
        {children}
      </p>
      <ContactButton title={title} contactOnly={contactOnly} />
      {updatedAt && (
        <div style={{ marginTop: '14px', fontSize: '11px', color: 'var(--ink-muted)' }}>
          最近更新：{updatedAt}
        </div>
      )}
    </div>
  )
}