export function nextTrack(queue, currentId, direction, mode, random = Math.random) {
  if (!queue.length) return null
  const index = queue.findIndex(track => track.id === currentId)
  if (mode === 'shuffle' && queue.length > 1 && index >= 0) {
    const candidates = queue.filter(track => track.id !== currentId)
    return candidates[Math.floor(random() * candidates.length)]
  }
  return queue[index < 0 ? (direction > 0 ? 0 : queue.length - 1)
    : (index + direction + queue.length) % queue.length]
}

export const playlistLink = token => `https://t.me/haotianzhaopian_bot?startapp=playlist_${token}`

export const musicArtist = artist => !artist || ['<unknown>', 'unknown'].includes(artist.toLowerCase()) ? '未知歌手' : artist

export function musicPlaybackError(code, status) {
  if (status === 404) return '歌曲不存在或已下架，请选择其他歌曲。'
  if (status === 503 || status === 429) return '音频服务暂时繁忙或上游限流，请稍后重试。'
  if (status >= 500) return '音频服务器连接异常，请稍后重试。'
  if (code === 3) return '音频解码失败，请重试；若仍失败，请联系管理员检查播放版本。'
  if (code === 4) return '音频格式暂不受当前客户端支持，或音频地址不可用，请稍后重试。'
  return '音频网络连接中断或超时，请检查网络后重试。'
}

export function prioritizeTrack(ids, trackId, currentId) {
  if (trackId === currentId) return ids
  return [trackId, ...ids.filter(id => id !== trackId)]
}
