import { announcementText } from '../utils/tenant'

export const ANNOUNCEMENT_TEXT = '充值模块开发中，商品购买暂未开放'

// 全屏时 Telegram 的关闭/菜单按钮会浮在页面顶部:用等高的公告栏避让;非全屏显示公告卡片
export function FullscreenAnnouncementBar() {
  return (
    <div style={{
      margin: '0 -12px 4px',
      paddingTop: 'var(--tg-safe-area-inset-top, 0px)',
      boxSizing: 'border-box'
    }}>
      <div
        aria-label="公告"
        style={{
          height: 'max(var(--tg-content-safe-area-inset-top, 0px), 44px)',
          padding: '0 64px',
          boxSizing: 'border-box',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '6px',
          fontSize: '13px',
          color: '#9a6b18',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis'
        }}
      >
        <span aria-hidden="true">📢</span>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{announcementText()}</span>
      </div>
    </div>
  )
}

export function AnnouncementCard() {
  return (
    <div
      aria-label="公告"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '8px 12px',
        marginBottom: '8px',
        background: '#fff',
        border: '1px solid rgba(154, 107, 24, 0.18)',
        borderLeft: '3px solid #c9a24d',
        borderRadius: '10px',
        boxShadow: '0 1px 3px rgba(120, 90, 50, 0.05)',
        fontSize: '12px',
        color: '#9a6b18',
        lineHeight: 1.4
      }}
    >
      <span aria-hidden="true">📢</span>
      <span style={{ fontWeight: 700 }}>公告</span>
      <span style={{ width: '1px', height: '12px', background: '#e6d3a3' }} />
      <span style={{ minWidth: 0, color: '#987f54', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {announcementText()}
      </span>
    </div>
  )
}
