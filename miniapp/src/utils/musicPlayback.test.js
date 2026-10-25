import test from 'node:test'
import assert from 'node:assert/strict'
import { musicArtist, musicPlaybackError, nextTrack, playlistLink, prioritizeTrack } from './musicPlayback.js'

const queue = [{ id: 1 }, { id: 2 }, { id: 3 }]
test('sequential playback stays in the selected list and wraps in both directions', () => {
  assert.equal(nextTrack([], 1, 1, 'sequential'), null)
  assert.equal(nextTrack(queue, 3, 1, 'sequential').id, 1)
  assert.equal(nextTrack(queue, 1, -1, 'sequential').id, 3)
  assert.equal(nextTrack(queue, 99, 1, 'sequential').id, 1)
  assert.equal(nextTrack(queue, 99, -1, 'sequential').id, 3)
  assert.equal(nextTrack([queue[1]], 2, 1, 'shuffle').id, 2)
})
test('shuffle excludes current track; manual next still advances in single mode', () => {
  assert.equal(nextTrack(queue, 2, 1, 'shuffle', () => 0).id, 1)
  assert.equal(nextTrack(queue, 2, 1, 'shuffle', () => 0.999).id, 3)
  assert.equal(nextTrack(queue, 2, 1, 'single').id, 3)
})
test('playlist links use Mini App startapp rather than opening the music channel', () => {
  assert.equal(playlistLink('a'.repeat(32)), `https://t.me/haotianzhaopian_bot?startapp=playlist_${'a'.repeat(32)}`)
})
test('play next deduplicates priority entries without changing the active track', () => {
  const original = [3, 2]
  assert.deepEqual(prioritizeTrack(original, 2, 1), [2, 3])
  assert.deepEqual(prioritizeTrack(original, 4, 1), [4, 3, 2])
  assert.equal(prioritizeTrack(original, 1, 1), original)
  assert.deepEqual(original, [3, 2])
})
test('unknown performer text is consistent in mini player, lists and full player', () => {
  for (const artist of [null, '', '<unknown>', 'UNKNOWN']) assert.equal(musicArtist(artist), '未知歌手')
  assert.equal(musicArtist('Adele'), 'Adele')
})

test('playback errors distinguish network, decoding, unavailable track and busy service', () => {
  assert.match(musicPlaybackError(2), /网络/)
  assert.match(musicPlaybackError(3), /解码/)
  assert.match(musicPlaybackError(4), /格式/)
  assert.match(musicPlaybackError(2, 404), /下架/)
  assert.match(musicPlaybackError(2, 503), /繁忙/)
  assert.match(musicPlaybackError(2, 502), /服务器/)
})
