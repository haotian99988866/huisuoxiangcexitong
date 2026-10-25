import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import ModuleTabs from '../components/ModuleTabs'
import { FullscreenAnnouncementBar } from '../components/Announcement'
import { useMusic } from '../components/MusicPlayer'
import MusicSheet from '../components/MusicDialog'
import MusicIcon from '../components/MusicIcon'
import { bindBackButton, hasTelegramBackButton, subscribeFullscreen } from '../utils/twa'
import { requestMusic } from '../utils/music'
import { musicArtist as artistName, playlistLink } from '../utils/musicPlayback'

export default function Music() {
  const player = useMusic()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const sharedToken = params.get('playlist')
  const [query, setQuery] = useState(() => sharedToken ? '' : player.browseState.current.query)
  const [searchInput, setSearchInput] = useState(query)
  const [isFullscreen, setIsFullscreen] = useState(() => Boolean(window.Telegram?.WebApp?.isFullscreen))
  const [shared, setShared] = useState(null)
  const [sharedError, setSharedError] = useState('')
  const [sharedAttempt, setSharedAttempt] = useState(0)
  const [view, setView] = useState(() => sharedToken ? 'shared' : player.browseState.current.view)
  const [sheet, setSheet] = useState(null)
  const [actionTrack, setActionTrack] = useState(null)
  const [name, setName] = useState('')
  const [rename, setRename] = useState('')
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [shareUrl, setShareUrl] = useState('')
  const [notice, setNotice] = useState('')
  const [pageNumber, setPageNumber] = useState(() => sharedToken ? 1 : player.browseState.current.page)
  const [jumpPage, setJumpPage] = useState(null)
  const [pageError, setPageError] = useState('')
  const [pickQuery, setPickQuery] = useState('')
  const [pickPage, setPickPage] = useState(1)
  const searchRef = useRef(null)
  const songsRef = useRef(null)
  const navigationRef = useRef(null)
  const previousFilter = useRef({ view, query })
  const browseRef = useRef(null)
  browseRef.current = { view, query, page: pageNumber }
  useLayoutEffect(() => {
    window.scrollTo(0, sharedToken ? 0 : player.browseState.current.scroll)
    return () => {
      if (browseRef.current.view !== 'shared') player.browseState.current = { ...browseRef.current, scroll: window.scrollY }
    }
  }, [player.browseState])
  const selected = player.playlists.find(item => `playlist:${item.id}` === view)
  const closeSheet = () => { setSheet(null); setDeleteConfirm(false) }
  const changeView = value => { setView(value); setQuery(''); setSearchInput(''); setNotice('') }

  useEffect(() => {
    if (player.status === 'idle') player.load()
  }, [player.status, player.load])
  useEffect(() => bindBackButton(() => navigate('/', { replace: true })), [navigate])
  useEffect(() => subscribeFullscreen(setIsFullscreen), [])
  useEffect(() => {
    if (!notice || sheet) return
    const timer = setTimeout(() => setNotice(''), 3200)
    return () => clearTimeout(timer)
  }, [notice, sheet])
  useEffect(() => {
    setRename(selected?.name || '')
    setDeleteConfirm(false)
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
  const source = ids ? ids.map(id => player.tracks.find(track => track.id === id)).filter(Boolean)
    : view === 'all' ? player.tracks : []
  const filtered = source.filter(track => [track.title, track.artist, track.album]
    .some(value => value.toLowerCase().includes(query.trim().toLowerCase())))
  const rowsPerPage = 20
  const pageCount = Math.max(1, Math.ceil(filtered.length / rowsPerPage))
  const currentPage = Math.min(pageNumber, pageCount)
  const pageTracks = filtered.slice((currentPage - 1) * rowsPerPage, currentPage * rowsPerPage)
  useEffect(() => {
    if (previousFilter.current.view === view && previousFilter.current.query === query) return
    previousFilter.current = { view, query }
    setPageNumber(1); setJumpPage(null); setPageError('')
  }, [view, query])
  useEffect(() => { setPageNumber(number => Math.min(number, pageCount)) }, [pageCount])
  const goToPage = number => {
    setPageNumber(number)
    setJumpPage(null)
    setPageError('')
    if (songsRef.current) window.scrollTo({
      top: window.scrollY + songsRef.current.getBoundingClientRect().top - navigationRef.current.offsetHeight - 12,
    })
  }
  const title = selected?.name || ({ all: '全部歌曲', favorites: '我喜欢', recent: '最近播放',
    playlists: '我的歌单', shared: shared?.name || '分享歌单' })[view] || '歌曲列表'
  const activeTab = selected ? 'playlists' : view
  const favorites = player.profile?.favorites || []
  const pickTracks = player.tracks.filter(track => `${track.title} ${track.artist}`.toLowerCase().includes(pickQuery.trim().toLowerCase()))
  const pickPages = Math.max(1, Math.ceil(pickTracks.length / 12))
  const safePickPage = Math.min(pickPage, pickPages)
  const savePlaylist = (playlist, fields) => player.accountRequest('music_playlist_save', {
    playlist_id: playlist.id, revision: playlist.revision, name: playlist.name,
    tracks: playlist.tracks.filter(id => player.tracks.some(track => track.id === id)), ...fields,
  })
  const share = async playlist => {
    const url = playlistLink(playlist.share_token)
    setShareUrl(url)
    setNotice('')
    setSheet('share')
    try {
      if (!navigator.clipboard?.writeText) throw new Error('当前客户端不支持复制')
      await navigator.clipboard.writeText(url)
      setNotice('链接已复制，发给朋友一起听')
    } catch (error) {
      console.warn('[Playlist clipboard]', error)
      setNotice('自动复制不可用，请长按链接手动复制')
    }
  }
  const clearSearch = () => {
    setSearchInput('')
    setQuery('')
  }
  const search = event => {
    event.preventDefault()
    setQuery(searchInput.trim())
    if (view === 'playlists') setView('all')
    setPageNumber(1)
    setJumpPage(null)
    setPageError('')
    searchRef.current?.blur()
  }

  return (
    <main className="music-page">
      <div ref={navigationRef} className="music-navigation">
        {isFullscreen && <FullscreenAnnouncementBar />}
        <ModuleTabs active="music" />
      {!hasTelegramBackButton && <button className="music-back" type="button" onClick={() => navigate('/')}>← 返回首页</button>}
      <form className="music-search" role="search" onSubmit={search}><MusicIcon name="search" size={19} />
        <input ref={searchRef} aria-label="搜索歌曲、歌手或专辑" type="search" enterKeyHint="search" value={searchInput} maxLength={120}
          onChange={event => setSearchInput(event.target.value)}
          placeholder="搜索歌名、歌手、专辑" />
        {(searchInput || query) && <button type="button" className="music-icon-button" aria-label="清空搜索" onClick={clearSearch}><MusicIcon name="close" size={16} /></button>}
        <button type="submit" className="music-search-submit" aria-label="确认搜索">搜索</button>
      </form>
      <nav className="music-tabs" aria-label="音乐分类">
        {[['all', '发现'], ['favorites', '我喜欢'], ['recent', '最近'], ['playlists', '歌单'],
          ...(sharedToken ? [['shared', '分享']] : [])].map(([key, label]) => (
          <button type="button" key={key} aria-current={activeTab === key ? 'page' : undefined}
            onClick={() => changeView(key)}>{label}</button>
        ))}
      </nav>
      </div>
      {player.accountError && <div role="alert" className="music-error">{player.accountError}
        <button type="button" disabled={player.busy} onClick={player.loadAccount}>重新同步</button></div>}
      {!player.profile && <div className="music-login-hint"><MusicIcon name="heart" size={16} />
        <p>在 Telegram 中打开，即可同步收藏和歌单。</p>
        {window.Telegram?.WebApp?.initData && <button type="button" disabled={player.busy} onClick={player.loadAccount}>同步</button>}
      </div>}
      {player.catalogError && <div role="alert" className="music-error">{player.catalogError}
        <button type="button" onClick={player.load}>重试</button></div>}

      {view === 'playlists' ? (
        <section className="music-playlists" aria-label="我的歌单">
          <header className="music-section-heading"><h2>我的歌单 <small>{player.playlists.length}</small></h2>
            <button type="button" className="music-text-button" onClick={() => { setNotice(''); setActionTrack(null); setName(''); setSheet('create') }} disabled={!player.profile}><MusicIcon name="plus" size={17} />新建</button></header>
          <div className="music-playlist-grid">{player.playlists.map((playlist, index) => (
            <button type="button" className="music-playlist-card" key={playlist.id} onClick={() => changeView(`playlist:${playlist.id}`)}>
              <span className={`music-playlist-art music-color-${index % 3}`} aria-hidden="true"><MusicIcon name="note" size={34} /></span>
              <strong>{playlist.name}</strong><small>{playlist.tracks.filter(id => player.tracks.some(track => track.id === id)).length} 首歌曲</small>
            </button>
          ))}</div>
          {!player.playlists.length && <div className="music-empty"><MusicIcon name="list" size={32} /><h3>为喜欢的音乐安个家</h3><p>创建歌单，收集歌曲，再分享给朋友。</p>
            {player.profile && <button className="music-primary" type="button" onClick={() => { setNotice(''); setActionTrack(null); setName(''); setSheet('create') }}>创建第一个歌单</button>}</div>}
        </section>
      ) : (
        <section ref={songsRef} aria-label="歌曲列表" className="music-songs">
          {selected && <button type="button" className="music-text-button" onClick={() => changeView('playlists')}>← 全部歌单</button>}
          <header className="music-section-heading"><div className="music-section-title"><h2>{title}</h2><p>{filtered.length} 首{view === 'shared' ? ' · 朋友分享，只读' : selected ? ' · 我的专属歌单' : ' · 点击歌曲播放'}</p></div>
            <div className="music-heading-actions">
              {selected && <button type="button" className="music-icon-button" aria-label="管理歌单" onClick={() => setSheet('manage')}><MusicIcon name="more" /></button>}
              {(selected || (view === 'shared' && shared)) && <button type="button" className="music-icon-button" aria-label="分享歌单" onClick={() => share(selected || shared)}><MusicIcon name="share" /></button>}
            </div>
          </header>
          {view === 'shared' && (sharedError ? <div role="alert" className="music-error">{sharedError}<button type="button" onClick={() => setSharedAttempt(value => value + 1)}>重试</button></div>
            : !shared ? <p role="status" className="music-hint">正在加载分享歌单…</p> : player.profile && <button type="button" className="music-text-button" disabled={player.busy || player.status !== 'ready'} onClick={async () => {
              const result = await player.accountRequest('music_playlist_create', { name: shared.name, tracks: source.map(track => track.id) })
              if (result) changeView(`playlist:${result.playlists[0].id}`)
            }}><MusicIcon name="plus" size={16} />保存为我的歌单</button>)}
          <div className="music-list-toolbar"><button type="button" className="music-text-button" disabled={!filtered.length} onClick={() => player.start(filtered[0], filtered)}><span className="music-small-play"><MusicIcon name="play" size={13} /></span>播放全部</button>
            {selected && <button type="button" className="music-text-button" onClick={() => { setPickQuery(''); setPickPage(1); setSheet('pick'); setNotice('') }}><MusicIcon name="plus" size={15} />添加歌曲</button>}
            <span>{player.status === 'loading' ? '更新中…' : query ? '搜索结果' : '在线歌库'}</span></div>
          {player.status === 'loading' && !player.tracks.length && <div className="music-skeleton" role="status">正在加载歌曲…</div>}
          {ids && source.length < ids.length && <p className="music-hint">已隐藏不存在或下架的歌曲。</p>}
          {!filtered.length && player.status === 'ready' && <div className="music-empty"><MusicIcon name={query ? 'search' : 'note'} size={32} />
            <h3>{query ? '没有找到这首歌' : '这里还没有歌曲'}</h3>
            <p>{query ? '试试更短的歌名，或换一个歌手。' : view === 'favorites' ? '点歌曲旁的爱心，把喜欢留下。' : view === 'recent' ? '播放过的歌曲，会在这里与你重逢。' : selected ? '去发现页，点击歌曲旁的「更多」添加。' : '稍后刷新，发现新的旋律。'}</p>
            {query && <button type="button" onClick={clearSearch}>清空搜索</button>}
          </div>}
          <ul className="music-list">{pageTracks.map((track, index) => (
            <li key={track.id} className={player.current?.id === track.id ? 'music-row is-current' : 'music-row'}>
              <button type="button" className="music-song-button" onClick={() => player.start(track, filtered)}
                aria-label={`播放 ${track.title} - ${artistName(track.artist)}`} aria-current={player.current?.id === track.id ? 'true' : undefined}>
                <span className="music-song-number" aria-hidden="true">{player.current?.id === track.id && player.playing ? '♫' : String((currentPage - 1) * rowsPerPage + index + 1).padStart(2, '0')}</span>
                <span className="music-track-copy"><strong>{track.title}</strong><small>{artistName(track.artist)}{track.album ? ` · ${track.album}` : ''}</small></span>
              </button>
              {player.profile && <button type="button" className="music-icon-button music-favorite" aria-label={`${favorites.includes(track.id) ? '取消收藏' : '收藏'} ${track.title}`}
                aria-pressed={favorites.includes(track.id)} disabled={player.busy} onClick={async () => {
                  const enabled = !favorites.includes(track.id)
                  if (await player.favorite(track.id, enabled)) setNotice(enabled ? '已加入我喜欢' : '已取消收藏')
                }}><MusicIcon name="heart" size={18} /></button>}
              <button type="button" className="music-icon-button" aria-label={`更多操作 ${track.title}`} onClick={() => { setNotice(''); setActionTrack(track); setSheet('track') }}><MusicIcon name="more" size={18} /></button>
            </li>
          ))}</ul>
          {pageCount > 1 && <nav className="music-pagination" aria-label="歌曲分页">
            <form className="music-page-jump" onSubmit={event => {
              event.preventDefault()
              const value = jumpPage ?? String(currentPage)
              const number = Number(value)
              if (!/^\d+$/.test(value) || !Number.isInteger(number) || number < 1 || number > pageCount) {
                setPageError(`请输入 1–${pageCount} 之间的整数页码`)
                return
              }
              goToPage(number)
            }}>
              <button type="button" aria-label="上一页" disabled={currentPage === 1} onClick={() => goToPage(currentPage - 1)}>‹</button>
              <label className="music-page-position"><span className="music-sr-only">当前页，共 {pageCount} 页</span>
                <input aria-label="跳转页码" inputMode="numeric" value={jumpPage ?? String(currentPage)}
                  onChange={event => { setJumpPage(event.target.value); setPageError('') }} maxLength={6}
                  aria-invalid={!!pageError} aria-describedby={pageError ? 'music-page-error' : undefined} />
                <span aria-hidden="true">/ {pageCount}</span>
              </label>
              <button type="submit" className="music-page-go">跳转</button>
              <button type="button" aria-label="下一页" disabled={currentPage === pageCount} onClick={() => goToPage(currentPage + 1)}>›</button>
            </form>
            {pageError && <p id="music-page-error" role="alert" className="music-error">{pageError}</p>}
          </nav>}
        </section>
      )}
      {notice && !sheet && <div className="music-toast" role="status"><MusicIcon name="check" size={17} />{notice}</div>}
      {sheet && <MusicSheet title={sheet === 'create' ? '创建歌单' : sheet === 'manage' ? '管理歌单' : sheet === 'share' ? '分享这份好听' : sheet === 'add' ? '添加到歌单' : sheet === 'pick' ? '选择歌曲' : '歌曲操作'} onClose={closeSheet}>
        {sheet === 'create' && <form onSubmit={async event => {
          event.preventDefault()
          const result = await player.accountRequest('music_playlist_create', { name, tracks: actionTrack ? [actionTrack.id] : [] })
          if (result) { changeView(`playlist:${result.playlists[0].id}`); setNotice(actionTrack ? '歌单已创建，歌曲已添加' : '歌单已创建'); closeSheet() }
        }}><label>歌单名称<input autoFocus required maxLength={80} value={name} onChange={event => setName(event.target.value)} placeholder="例如：下班路上的歌" /></label>
          <p className="music-hint">给你的音乐收藏起个名字，最多 80 个字。</p>
          {actionTrack && <p className="music-hint">创建后会同时添加「{actionTrack.title}」。</p>}
          <button className="music-primary music-wide" type="submit" disabled={player.busy || !name.trim()}>{player.busy ? '正在保存…' : '创建歌单'}</button></form>}
        {sheet === 'manage' && selected && <>
          <form onSubmit={async event => { event.preventDefault(); if (await savePlaylist(selected, { name: rename })) { setNotice('歌单名称已更新'); closeSheet() } }}>
            <label>歌单名称<input autoFocus required maxLength={80} value={rename} onChange={event => setRename(event.target.value)} /></label>
            <button type="submit" className="music-primary music-wide" disabled={player.busy || !rename.trim()}>保存名称</button>
          </form>
          <p className="music-hint">每个歌单最多 200 首。分享后，朋友可听不可改；请勿在名称中填写隐私信息。</p>
          {!deleteConfirm ? <button type="button" className="music-danger music-wide" disabled={player.busy} onClick={() => setDeleteConfirm(true)}>删除歌单</button>
            : <div className="music-delete-confirm" role="alert"><p>确认删除「{selected.name}」？分享链接也会失效。</p>
              <button type="button" className="music-danger" disabled={player.busy} onClick={async () => {
                if (await player.accountRequest('music_playlist_delete', { playlist_id: selected.id, revision: selected.revision })) { changeView('playlists'); setNotice('歌单已删除'); closeSheet() }
              }}>确认删除</button><button type="button" onClick={() => setDeleteConfirm(false)}>取消</button></div>}
        </>}
        {sheet === 'track' && actionTrack && <>
          <div className="music-sheet-track"><div className="music-art"><MusicIcon name="note" size={28} /></div><div><h3>{actionTrack.title}</h3><p>{artistName(actionTrack.artist)}</p></div></div>
          <button type="button" className="music-sheet-option" onClick={() => { player.start(actionTrack, filtered); closeSheet() }}><MusicIcon name="play" />播放这首歌</button>
          <button type="button" className="music-sheet-option" disabled={!player.current || player.current.id === actionTrack.id} onClick={() => {
            player.playNext(actionTrack); setNotice(`下一首播放：${actionTrack.title}`); closeSheet()
          }}><MusicIcon name="next" />下一首播放</button>
          {player.profile ? <>
            <button type="button" className="music-sheet-option" disabled={player.busy} onClick={async () => {
              const enabled = !favorites.includes(actionTrack.id)
              if (await player.favorite(actionTrack.id, enabled)) { setNotice(enabled ? '已加入我喜欢' : '已取消收藏'); closeSheet() }
            }}><MusicIcon name="heart" />{favorites.includes(actionTrack.id) ? '取消收藏' : '收藏歌曲'}</button>
            <button type="button" className="music-sheet-option" onClick={() => setSheet('add')}><MusicIcon name="plus" />添加到歌单</button>
            {selected && <button type="button" className="music-sheet-option music-danger" disabled={player.busy} onClick={async () => {
              if (await savePlaylist(selected, { tracks: source.filter(track => track.id !== actionTrack.id).map(track => track.id) })) { setNotice('已从歌单移除'); closeSheet() }
            }}>从当前歌单移除</button>}
          </> : <p className="music-hint">从 Telegram 打开后，即可收藏歌曲并添加到自己的歌单。</p>}
        </>}
        {sheet === 'add' && actionTrack && <>
            <button type="button" className="music-sheet-option" onClick={() => setSheet('track')}>← 返回歌曲操作</button>
            <p className="music-hint">将「{actionTrack.title}」加入一个歌单</p>
            {player.playlists.map(playlist => <button type="button" className="music-sheet-option" key={playlist.id} disabled={player.busy || playlist.tracks.includes(actionTrack.id)}
              onClick={async () => {
                const tracks = [...playlist.tracks.filter(id => player.tracks.some(item => item.id === id)), actionTrack.id]
                if (await savePlaylist(playlist, { tracks })) { setNotice(`已添加到「${playlist.name}」`); closeSheet() }
              }}><MusicIcon name="list" /><span>{playlist.name}</span><small>{playlist.tracks.includes(actionTrack.id) ? '已添加' : `${playlist.tracks.length} 首`}</small></button>)}
            <button type="button" className="music-sheet-option" onClick={() => { setNotice(''); setName(''); setSheet('create') }}><MusicIcon name="plus" />创建新歌单</button>
        </>}
        {sheet === 'pick' && selected && <>
          <p className="music-hint">为「{selected.name}」添加歌曲，已添加的歌曲不会重复加入。</p>
          <input className="music-pick-search" aria-label="搜索可添加歌曲" type="search" placeholder="搜索歌名或歌手"
            value={pickQuery} onChange={event => { setPickQuery(event.target.value); setPickPage(1) }} />
          {pickTracks.slice((safePickPage - 1) * 12, safePickPage * 12).map(track => (
            <button type="button" className="music-sheet-option" key={track.id} aria-label={`添加 ${track.title}`}
              disabled={player.busy || selected.tracks.includes(track.id)} onClick={async () => {
                const tracks = [...selected.tracks.filter(id => player.tracks.some(item => item.id === id)), track.id]
                if (await savePlaylist(selected, { tracks })) setNotice(`已添加「${track.title}」`)
              }}><span>{track.title}<small className="music-pick-artist">{artistName(track.artist)}</small></span>
              <MusicIcon name={selected.tracks.includes(track.id) ? 'check' : 'plus'} size={18} /></button>
          ))}
          {!pickTracks.length && <p className="music-hint">没有找到匹配歌曲。</p>}
          <div className="music-pick-pages"><button type="button" disabled={safePickPage === 1} onClick={() => setPickPage(safePickPage - 1)}>上一页</button>
            <span>{safePickPage} / {pickPages}</span><button type="button" disabled={safePickPage === pickPages} onClick={() => setPickPage(safePickPage + 1)}>下一页</button></div>
          <button type="button" className="music-primary music-wide" onClick={closeSheet}>完成添加</button>
        </>}
        {sheet === 'share' && <>
          <div className="music-share-art"><MusicIcon name="share" size={32} /></div>
          <p className="music-hint">分享的不只是歌单，还有你的音乐品味。朋友打开链接即可听到最新内容。</p>
          <label>分享链接<input aria-label="分享链接" readOnly value={shareUrl} onFocus={event => event.target.select()} /></label>
          <button type="button" className="music-primary music-wide" onClick={() => {
            try {
              if (!window.Telegram?.WebApp?.openTelegramLink) throw new Error('请复制链接后在 Telegram 分享')
              window.Telegram.WebApp.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(shareUrl)}&text=${encodeURIComponent('来听听这个歌单')}`)
            } catch (error) { console.error('[Playlist share]', error); setNotice(error.message) }
          }}>通过 Telegram 分享</button>
        </>}
        {notice && <p className="music-notice" role="status">{notice}</p>}
        {player.accountError && <p role="alert" className="music-error">{player.accountError}</p>}
      </MusicSheet>}
    </main>
  )
}
