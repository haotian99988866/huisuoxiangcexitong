import test from 'node:test'
import assert from 'node:assert/strict'
import { filterMusicTracks, musicCategories, musicCategory } from './musicCategories.js'

test('single-language sources classify romanized titles without splitting a mixed source by channel', () => {
  assert.equal(musicCategory({ title: 'Song', album: '英文音乐' }), 'en')
  assert.equal(musicCategory({ title: 'Song', album: '缅甸音乐' }), 'my')
  assert.equal(musicCategory({ title: 'Song', album: '傣族音乐' }), 'tai')
  assert.equal(musicCategory({ title: '后来', album: '中文英文' }), 'zh')
  assert.equal(musicCategory({ title: 'Song', album: '中文英文' }), 'other')
})

test('uncertain language stays in other rather than guessing from Latin characters or performer names', () => {
  for (const track of [
    {}, { title: '未标注歌名（SYNChina·2）' }, { title: 'Song', artist: '周杰伦' },
    { title: '日本の歌' }, { title: '한국歌曲' }, { title: 'မြန်မာ歌曲' },
  ]) assert.equal(musicCategory(track), 'other')
  assert.equal(musicCategory({ title: '晴天 (Live)' }), 'zh')
})

test('category filtering searches the full category and keeps order, references and input intact', () => {
  const tracks = Array.from({ length: 45 }, (_, index) => ({
    id: index + 1, title: `歌曲 ${index + 1}`, artist: 'Artist', album: '',
  }))
  tracks.push({ id: 46, title: '歌曲 46', artist: 'Artist', album: '英文音乐' })
  assert.equal(filterMusicTracks(tracks, 'all', '').length, 46)
  assert.equal(filterMusicTracks(tracks, 'zh', '').length, 45)
  assert.deepEqual(filterMusicTracks(tracks, 'zh', '  歌曲 45  '), [tracks[44]])
  assert.deepEqual(filterMusicTracks(tracks, 'en', 'artist'), [tracks[45]])
  assert.deepEqual(filterMusicTracks(tracks, 'my', ''), [])
  assert.equal(filterMusicTracks(tracks, 'zh', '')[0], tracks[0])
  assert.equal(tracks.length, 46)
  assert.equal(new Set(musicCategories.map(([key]) => key)).size, 6)
})
