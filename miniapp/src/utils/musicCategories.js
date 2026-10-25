export const musicCategories = [
  ['all', '全部'], ['zh', '华语'], ['en', '英语'],
  ['my', '缅甸语'], ['tai', '傣语'], ['other', '其他'],
]

const sourceCategories = new Map([['英文音乐', 'en'], ['缅甸音乐', 'my'], ['傣族音乐', 'tai']])

export function musicCategory(track) {
  const sourceCategory = sourceCategories.get(track.album)
  if (sourceCategory) return sourceCategory
  const title = (track.title || '').replace(/（(?:SYNChina|daige2005|syn119|yingwen2005)·\d+）$/u, '')
  if (title !== '未标注歌名' && /\p{Script=Han}/u.test(title)
    && !/[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\p{Script=Myanmar}]/u.test(title)) return 'zh'
  return 'other'
}

export function filterMusicTracks(tracks, category, query) {
  const term = query.trim().toLowerCase()
  return tracks.filter(track => (category === 'all' || musicCategory(track) === category)
    && [track.title, track.artist, track.album].some(value => (value || '').toLowerCase().includes(term)))
}
