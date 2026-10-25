import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import ModuleTabs from '../components/ModuleTabs'
import { FullscreenAnnouncementBar } from '../components/Announcement'
import { musicTime, useMusic } from '../components/MusicPlayer'
import { bindBackButton, hasTelegramBackButton, subscribeFullscreen } from '../utils/twa'
import { requestMusic } from '../utils/music'
import { playlistLink } from '../utils/musicPlayback'

export default function Music() {
  const player = useMusic()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [params] = useSearchParams()
  const sharedToken = params.get('playlist')
  const [shared, setShared] = useState(null)
  const [sharedError, setSharedError] = useState('')
  const [sharedAttempt, setSharedAttempt] = useState(0)
  const [view, setView] = useState(sharedToken ? 'shared' : 'all')
  const [name, setName] = useState('')
  const [rename, setRename] = useState('')
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [shareUrl, setShareUrl] = useState('')
  const [notice, setNotice] = useState('')
  const selected = player.playlists.find(item => `playlist:${item.id}` === view)
  useEffect(() => {
    if (player.status === 'idle') player.load()
  }, [player.status, player.load])
  useEffect(() => bindBackButton(() => navigate('/', { replace: true })), [navigate])
  useEffect(() => subscribeFullscreen(setIsFullscreen), [])
  useEffect(() => {
    setRename(selected?.name || '')
    setDeleteConfirm(false)
    setShareUrl('')
  }, [selected?.id, selected?.name])
  useEffect(() => {
    if (!sharedToken) { setShared(null); return }
    let active = true
    setShared(null)
    setSharedError('')
    setView('shared')
    requestMusic('music_shared', { token: sharedToken }).then(result => {
      if (active) setShared(result.playlist)
    }).catch(error => {
      console.error('[Shared playlist]', error)
      if (active) setSharedError(error.message)
    })
    return () => { active = false }
  }, [sharedToken, sharedAttempt])
  const ids = selected?.tracks || (view === 'shared' ? shared?.tracks
    : view === 'favorites' ? player.profile?.favorites : view === 'recent' ? player.profile?.recent : null)
  const source = ids ? ids.map(id => player.tracks.find(track => track.id === id)).filter(Boolean) : view === 'all' ? player.tracks : []
  const filtered = source.filter(track => [track.title, track.artist, track.album]
    .some(value => value.toLowerCase().includes(query.trim().toLowerCase())))
  const savePlaylist = (playlist, fields) => player.accountRequest('music_playlist_save', {
    playlist_id: playlist.id, revision: playlist.revision, name: playlist.name,
    tracks: playlist.tracks.filter(id => player.tracks.some(track => track.id === id)), ...fields,
  })
  const share = async playlist => {
    const url = playlistLink(playlist.share_token)
    setShareUrl(url)
    setNotice('')
    try {
      if (!navigator.clipboard?.writeText) throw new Error('当前客户端不支持复制')
      await navigator.clipboard.writeText(url)
      setNotice('歌单链接已复制，可发送给朋友')
    } catch (error) {
      console.warn('[Playlist clipboard]', error)
      setNotice('自动复制不可用，请长按下方链接手动复制')
    }
  }

  return (
    <main className="music-page">
      {isFullscreen && <FullscreenAnnouncementBar />}
      <ModuleTabs active="music" />
      {!hasTelegramBackButton && <button type="button" onClick={() => navigate('/')}>← 返回首页</button>}
      <div className="music-heading"><h1>音乐</h1>
        <button type="button" onClick={player.load} disabled={player.status === 'loading'}>刷新歌库</button>
      </div>
      <p className="music-hint">点击歌曲开始播放。锁屏、切后台或关闭小程序后，播放可能中断。</p>
      <section className="music-library" aria-label="我的音乐">
        <h2>我的音乐</h2>
        {!player.profile && <p className="music-hint">从 Telegram 打开并验证身份后，可收藏、创建歌单并同步最近播放和进度。</p>}
        <button type="button" onClick={player.loadAccount} disabled={player.busy}>刷新我的音乐</button>
        {player.accountError && <p role="alert" className="music-error">{player.accountError}</p>}
        <label>浏览范围<select value={view} onChange={event => { setView(event.target.value); setQuery(''); setShareUrl(''); setNotice('') }}>
          <option value="all">全部歌曲</option>
          {player.profile && <><option value="favorites">我喜欢</option><option value="recent">最近播放</option></>}
          {player.playlists.map(item => <option key={item.id} value={`playlist:${item.id}`}>{item.name}</option>)}
          {sharedToken && <option value="shared">朋友分享的歌单</option>}
        </select></label>
        {player.profile && <form onSubmit={async event => {
          event.preventDefault()
          const result = await player.accountRequest('music_playlist_create', { name })
          if (result) { setName(''); setView(`playlist:${result.playlists[0].id}`) }
        }}>
          <label>创建歌单<input required maxLength={80} value={name} onChange={event => setName(event.target.value)} placeholder="输入歌单名称" /></label>
          <button type="submit" disabled={player.busy || !name.trim()}>创建歌单</button>
        </form>}
        {selected && <>
          <form onSubmit={event => { event.preventDefault(); savePlaylist(selected, { name: rename }) }}>
            <label>歌单名称<input required maxLength={80} value={rename} onChange={event => setRename(event.target.value)} /></label>
            <button disabled={player.busy || !rename.trim()} type="submit">保存名称</button>
          </form>
          <div className="music-actions">
            <button type="button" onClick={() => share(selected)}>分享歌单</button>
            <button type="button" onClick={() => setDeleteConfirm(true)} disabled={player.busy}>删除歌单</button>
          </div>
          {deleteConfirm && <div role="alert">
            <p>删除后，分享链接也将失效。确认删除？</p>
            <button type="button" disabled={player.busy} onClick={async () => {
              if (await player.accountRequest('music_playlist_delete', { playlist_id: selected.id, revision: selected.revision })) setView('all')
            }}>确认删除</button>
            <button type="button" onClick={() => setDeleteConfirm(false)}>取消</button>
          </div>}
          <p className="music-hint">歌单最多 200 首，持链接者可查看和播放最新内容，只有你能修改；请勿在名称中填写隐私信息。</p>
        </>}
        {view === 'shared' && <>
          {sharedError ? <p role="alert" className="music-error">{sharedError} <button type="button" onClick={() => setSharedAttempt(value => value + 1)}>重试</button></p>
            : !shared ? <p role="status">正在加载分享歌单…</p> : <>
              <h3>{shared.name}</h3><p className="music-hint">朋友分享的歌单（只读）</p>
              <button type="button" onClick={() => share(shared)}>分享此歌单</button>
              {player.profile && <button type="button" disabled={player.busy || player.status !== 'ready'} onClick={async () => {
                const result = await player.accountRequest('music_playlist_create', { name: shared.name, tracks: source.map(track => track.id) })
                if (result) setView(`playlist:${result.playlists[0].id}`)
              }}>保存为我的歌单</button>}
            </>}
        </>}
        {shareUrl && <label>分享链接<input aria-label="分享链接" readOnly value={shareUrl} onFocus={event => event.target.select()} />
          <button type="button" onClick={() => {
            try {
              if (!window.Telegram?.WebApp?.openTelegramLink) throw new Error('请复制链接后在 Telegram 分享')
              window.Telegram.WebApp.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(shareUrl)}&text=${encodeURIComponent('来听听这个歌单')}`)
            } catch (error) { console.error('[Playlist share]', error); setNotice(error.message) }
          }}>通过 Telegram 分享</button>
        </label>}
        {notice && <p role="status">{notice}</p>}
      </section>
      <label className="music-search">搜索歌曲、歌手或专辑
        <input type="search" value={query} maxLength={120} onChange={event => setQuery(event.target.value)}
          placeholder="输入歌名或歌手" />
      </label>
      {player.catalogError && <p role="alert" className="music-error">{player.catalogError}</p>}
      {player.status === 'loading' && <p role="status">正在加载歌曲…</p>}
      {player.current && (
        <section className="music-now" aria-label="当前播放">
          <div className="music-art" aria-hidden="true">♪</div>
          <h2>{player.current.title}</h2>
          <p>{player.current.artist}{player.current.album ? ` · ${player.current.album}` : ''}</p>
          <label>播放进度
            <input type="range" min="0" max={player.duration || 0} step="0.1"
              disabled={!player.duration} value={Math.min(player.position, player.duration)}
              onChange={event => player.seek(event.target.value)} />
          </label>
          <div className="music-time"><span>{musicTime(player.position)}</span><span>{musicTime(player.duration)}</span></div>
          <div className="music-actions">
            <button type="button" onClick={() => player.step(-1)} disabled={!player.queue.length}>上一首</button>
            <button type="button" className="music-primary" onClick={player.toggle}>
              {player.waiting ? '取消加载' : player.playing ? '暂停' : '播放'}
            </button>
            <button type="button" onClick={() => player.step(1)} disabled={!player.queue.length}>下一首</button>
          </div>
          <div className="music-actions">
            <label>播放模式<select value={player.mode} disabled={!player.profile || player.busy} onChange={event => player.changeMode(event.target.value)}>
              <option value="sequential">顺序循环</option><option value="shuffle">随机播放</option><option value="single">单曲循环</option>
            </select></label>
            <button type="button" onClick={player.stop}>停止播放</button>
          </div>
          {window.Telegram?.WebApp?.platform === 'ios' || /iPad|iPhone|iPod/.test(navigator.userAgent)
            ? <p className="music-hint">音量请使用手机音量键调节。</p>
            : <label>音量<input type="range" min="0" max="1" step="0.05" value={player.volume}
              onChange={event => player.changeVolume(event.target.value)} /></label>}
          {player.waiting && <p role="status">正在缓冲音频…</p>}
          {player.error && <p role="alert" className="music-error">{player.error}</p>}
        </section>
      )}
      {player.status === 'ready' && player.tracks.length === 0 && (
        <section className="music-empty"><h2>暂未上架音乐</h2><p>管理员录入已获授权的音频后，将在这里展示。</p></section>
      )}
      {player.tracks.length > 0 && (
        <section aria-label="歌曲列表">
          <h2>{selected?.name || (view === 'shared' ? shared?.name || '分享歌单' : view === 'favorites' ? '我喜欢' : view === 'recent' ? '最近播放' : '歌曲列表')} <small>({filtered.length})</small></h2>
          <button type="button" disabled={!filtered.length} onClick={() => player.start(filtered[0], filtered)}>播放此列表</button>
          {ids && source.length < ids.length && <p className="music-hint">已隐藏不存在或下架的歌曲。</p>}
          {filtered.length === 0 && <p>{source.length ? '没有找到匹配歌曲，请换个关键词。' : '此列表暂无可播放歌曲，可切换到全部歌曲浏览。'}</p>}
          <ul className="music-list">{filtered.map(track => (
            <li key={track.id}><button type="button" onClick={() => player.start(track, filtered)}
              aria-label={`播放 ${track.title} - ${track.artist}`}
              aria-current={player.current?.id === track.id ? 'true' : undefined}>
              <span aria-hidden="true">♪</span>
              <span><strong>{track.title}</strong><small>{track.artist}{track.album ? ` · ${track.album}` : ''}</small></span>
              <span>{player.current?.id === track.id && player.playing ? '播放中' : '播放'}</span>
            </button>
              {player.profile && <div className="music-track-tools">
                <button type="button" aria-pressed={player.profile.favorites.includes(track.id)} disabled={player.busy}
                  onClick={() => player.favorite(track.id, !player.profile.favorites.includes(track.id))}>
                  {player.profile.favorites.includes(track.id) ? '取消收藏' : '收藏'}
                </button>
                <select aria-label={`将 ${track.title} 添加到歌单`} value="" disabled={player.busy}
                  onChange={event => {
                    const playlist = player.playlists.find(item => item.id === Number(event.target.value))
                    if (playlist) savePlaylist(playlist, { tracks: [...playlist.tracks.filter(id => player.tracks.some(item => item.id === id)), track.id] })
                  }}>
                  <option value="">添加到歌单</option>
                  {player.playlists.filter(item => !item.tracks.includes(track.id)).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
                {selected && <button type="button" disabled={player.busy} onClick={() => savePlaylist(selected, { tracks: source.filter(item => item.id !== track.id).map(item => item.id) })}>移出歌单</button>}
              </div>}
            </li>
          ))}</ul>
        </section>
      )}
    </main>
  )
}
