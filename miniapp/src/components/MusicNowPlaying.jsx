import { useState } from 'react'
import { musicTime, useMusic } from './MusicPlayer'
import MusicDialog from './MusicDialog'
import MusicIcon from './MusicIcon'
import { musicArtist } from '../utils/musicPlayback'

const modes = { sequential: '顺序循环', shuffle: '随机播放', single: '单曲循环' }

export default function MusicNowPlaying() {
  const player = useMusic()
  const [pane, setPane] = useState('player')
  const [scrub, setScrub] = useState(null)
  const [scrubTrack, setScrubTrack] = useState(null)
  const track = player.current
  const queueCount = new Set([...player.queue, ...player.nextUp].map(item => item.id)).size
  const favorite = player.profile?.favorites.includes(track.id)
  const position = scrub !== null && scrubTrack === track.id ? scrub : player.position
  const isIos = window.Telegram?.WebApp?.platform === 'ios' || /iPad|iPhone|iPod/.test(navigator.userAgent)
  const commitSeek = value => {
    player.seek(value)
    setScrub(null)
  }
  return (
    <MusicDialog full title={pane === 'queue' ? '播放队列' : pane === 'settings' ? '播放设置' : '正在播放'} onClose={() => player.setExpanded(false)}>
      {pane === 'player' ? <div className="music-playback-screen">
        <div className={`music-full-art${player.playing ? ' is-playing' : ''}`} aria-hidden="true">
          <div className={`music-vinyl${player.playing ? ' is-playing' : ''}`}><span>♪</span></div>
          <span className="music-art-caption">LET THE MUSIC STAY</span>
        </div>
        <div className="music-full-track">
          <div><h2>{track.title}</h2><p>{musicArtist(track.artist)}{track.album ? ` · ${track.album}` : ''}</p></div>
          <button type="button" className="music-icon-button music-favorite" disabled={!player.profile || player.busy}
            aria-label={favorite ? '取消收藏当前歌曲' : '收藏当前歌曲'} aria-pressed={!!favorite}
            onClick={() => player.favorite(track.id, !favorite)}><MusicIcon name="heart" size={24} /></button>
        </div>
        <div className="music-full-timeline"><input className="music-full-progress" type="range" aria-label="播放进度" min="0" max={player.duration || 0} step="0.1"
          disabled={!player.duration} value={Math.min(position, player.duration)}
          onChange={event => { setScrub(Number(event.target.value)); setScrubTrack(track.id) }}
          onPointerUp={event => commitSeek(event.currentTarget.value)}
          onPointerCancel={() => setScrub(null)}
          onKeyUp={event => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) commitSeek(event.currentTarget.value) }}
          onBlur={event => { if (scrub !== null && scrubTrack === track.id) commitSeek(event.currentTarget.value) }} />
        <div className="music-time"><span>{musicTime(position)}</span><span>{musicTime(player.duration)}</span></div></div>
        <div className="music-full-controls">
          <button type="button" className="music-icon-button" aria-label="上一首" disabled={!player.queue.length} onClick={() => player.step(-1)}><MusicIcon name="previous" size={28} /></button>
          <button type="button" className="music-main-toggle" aria-label={player.waiting ? '取消加载' : player.playing ? '暂停' : '播放'} onClick={player.toggle}>
            <MusicIcon name={player.playing || player.waiting ? 'pause' : 'play'} size={30} /></button>
          <button type="button" className="music-icon-button" aria-label="下一首" disabled={!player.queue.length && !player.nextUp.length} onClick={() => player.step(1)}><MusicIcon name="next" size={28} /></button>
        </div>
        <div className="music-full-tools">
          <label className="music-mode-label">播放模式<select aria-label="播放模式" value={player.mode} disabled={!player.profile || player.busy}
            onChange={event => player.changeMode(event.target.value)}>
            {Object.entries(modes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select></label>
          <button type="button" onClick={() => setPane('queue')}><MusicIcon name="list" size={19} />队列 <small>{queueCount}</small></button>
          <button type="button" className="music-icon-button" aria-label="更多设置" onClick={() => setPane('settings')}><MusicIcon name="more" /></button>
        </div>
      </div> : pane === 'settings' ? <div className="music-full-pane music-full-options">
        <button type="button" className="music-sheet-option" onClick={() => setPane('player')}><MusicIcon name="previous" size={16} />返回播放界面</button>
        {!player.profile && <p className="music-hint">在 Telegram 中登录后，可收藏歌曲并同步播放模式。</p>}
          {isIos ? <p className="music-hint">音量请使用手机音量键调节。</p>
            : <label>音量<input type="range" aria-label="音量" min="0" max="1" step="0.05" value={player.volume} onChange={event => player.changeVolume(event.target.value)} /></label>}
          <p className="music-hint">锁屏、切后台或关闭小程序后，播放可能中断。</p>
          <button type="button" className="music-stop-button" onClick={player.stop}>停止播放并清除进度</button>
      </div> : <div className="music-full-pane">
        <button type="button" className="music-sheet-option" onClick={() => setPane('player')}><MusicIcon name="previous" size={16} />返回播放界面</button>
        <p className="music-hint">{modes[player.mode]} · {queueCount} 首 · 队列仅用于本次播放，不会修改歌单。</p>
        <div className="music-queue-current"><span>当前歌曲</span><strong>{track.title}</strong><small>{musicArtist(track.artist)}</small></div>
        {player.nextUp.length > 0 && <section aria-label="优先播放"><h3>接下来优先播放</h3>
          {player.nextUp.map(item => <div className="music-queue-row" key={item.id}>
            <button type="button" onClick={() => player.playQueued(item)}><strong>{item.title}</strong><small>{musicArtist(item.artist)}</small></button>
            <button className="music-icon-button" type="button" aria-label={`移出优先队列 ${item.title}`} onClick={() => player.removeNext(item.id)}><MusicIcon name="close" size={17} /></button>
          </div>)}</section>}
        <section aria-label="当前播放列表"><h3>当前播放列表</h3>
          {player.queue.map(item => <div key={item.id} className={`music-queue-row${item.id === track.id ? ' is-current' : ''}`}>
            <button type="button" aria-label={`队列播放 ${item.title}`} onClick={() => player.playQueued(item)}>
              <strong>{item.title}</strong><small>{item.id === track.id ? '当前歌曲 · ' : ''}{musicArtist(item.artist)}</small>
            </button>
            <button className="music-icon-button" type="button" aria-label={`移出播放队列 ${item.title}`} disabled={item.id === track.id} onClick={() => player.removeFromQueue(item.id)}><MusicIcon name="close" size={17} /></button>
          </div>)}
          {!player.queue.length && <p className="music-hint">队列为空，可收起播放器去选择歌曲。</p>}
        </section>
      </div>}
      {(player.waiting || player.error || player.accountError) && <div className="music-full-feedback">
        {player.waiting && <p role="status" className="music-hint">正在缓冲，仍可点击暂停取消…</p>}
        {player.error && <p role="alert" className="music-error">{player.error}</p>}
        {player.accountError && <p role="alert" className="music-error">{player.accountError}</p>}
      </div>}
    </MusicDialog>
  )
}
