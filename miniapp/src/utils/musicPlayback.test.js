import test from 'node:test'
import assert from 'node:assert/strict'
import { nextTrack, playlistLink } from './musicPlayback.js'

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
