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

export function prioritizeTrack(ids, trackId, currentId) {
  if (trackId === currentId) return ids
  return [trackId, ...ids.filter(id => id !== trackId)]
}
