import lunarCalendar from 'lunar-javascript'

const { Solar } = lunarCalendar
const dateFormat = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
})
const lunarFestivals = new Map([
  ['1-1', '春节'], ['1-15', '元宵节'], ['5-5', '端午节'],
  ['7-7', '七夕'], ['8-15', '中秋节'], ['9-9', '重阳节'], ['12-8', '腊八节'],
])
const solarFestivals = new Map([['1-1', '元旦'], ['10-1', '国庆节']])
const seasons = [
  { key: 'spring', terms: ['立春', '雨水', '惊蛰', '春分', '清明', '谷雨'], phrase: '春有来信，日子常新', detail: '把忙碌放慢一点，发现生活里的新意。' },
  { key: 'summer', terms: ['立夏', '小满', '芒种', '夏至', '小暑', '大暑'], phrase: '夏日悠长，自有清欢', detail: '听一首喜欢的歌，留一段自在的时光。' },
  { key: 'autumn', terms: ['立秋', '处暑', '白露', '秋分', '寒露', '霜降'], phrase: '人间好时节，生活有回响', detail: '一曲音乐，一份近况，把日常过得有滋有味。' },
  { key: 'winter', terms: ['立冬', '小雪', '大雪', '冬至', '小寒', '大寒'], phrase: '岁暮有暖，日常有光', detail: '无论身在何处，愿熟悉的声音陪伴你。' },
]
const festivalGreetings = new Map([
  ['春节', '新岁启封，万事可期'], ['除夕', '灯火可亲，岁岁团圆'],
  ['元宵节', '一夜花灯，满城圆满'], ['端午节', '粽有牵挂，岁岁安康'],
  ['七夕', '心有相逢，日子有甜'], ['中秋节', '月圆人相望，天涯共此时'],
  ['重阳节', '岁岁登高，久久安康'], ['腊八节', '一碗温暖，年味渐浓'],
  ['元旦', '新的一年，愿你常有好事'], ['国庆节', '山河锦绣，共赴美好'],
])

// Dark accent colors keep white button labels readable in every theme.
const termStyles = [
  ['立春', '#416449', '#f7f7ee', '#dce7cd', 'blossom', '东风解冻，春意初生'],
  ['雨水', '#39636a', '#f3f7f4', '#d5e6e2', 'rain', '细雨润物，草木萌动'],
  ['惊蛰', '#4e653b', '#f7f8ee', '#dce8c7', 'leaf', '春雷初响，万物醒来'],
  ['春分', '#4b6851', '#f8f7ef', '#dee8d7', 'blossom', '昼夜平分，春色正好'],
  ['清明', '#3c6258', '#f3f6f0', '#d4e2d5', 'leaf', '清风寄念，草木清明'],
  ['谷雨', '#52633b', '#f8f7ed', '#e1e6c8', 'rain', '雨生百谷，春将归去'],
  ['立夏', '#326354', '#f3f7ed', '#d6e7d4', 'leaf', '绿意渐浓，夏日初长'],
  ['小满', '#5b6337', '#f9f8ed', '#e6e8c7', 'wheat', '小得盈满，恰是从容'],
  ['芒种', '#6b5c30', '#faf7eb', '#ece1bd', 'wheat', '麦香入夏，耕耘有时'],
  ['夏至', '#356571', '#f1f7f6', '#d4e7e8', 'sun', '日长之至，清风入怀'],
  ['小暑', '#376653', '#f4f8ef', '#dce9d2', 'lotus', '荷风送香，静享清凉'],
  ['大暑', '#365e60', '#f1f7f2', '#d4e5dc', 'lotus', '盛夏听雨，心有清凉'],
  ['立秋', '#6c5b36', '#faf7ee', '#eee2c9', 'leaf', '一叶知秋，风来有信'],
  ['处暑', '#58643e', '#f8f7ef', '#e1e4cb', 'reed', '暑气渐止，天地渐清'],
  ['白露', '#486574', '#f3f7f7', '#dce7e9', 'reed', '露白风清，秋意渐浓'],
  ['秋分', '#715831', '#faf6ec', '#eee0c3', 'wheat', '秋色平分，收获有期'],
  ['寒露', '#586074', '#f5f6f7', '#dfe3eb', 'chrysanthemum', '露寒秋深，菊有清香'],
  ['霜降', '#75513e', '#faf5ee', '#ecddce', 'leaf', '霜染层林，秋色入画'],
  ['立冬', '#475f5c', '#f4f6f2', '#dce3da', 'cloud', '冬始万物藏，心有暖阳'],
  ['小雪', '#506576', '#f3f6f8', '#dde6ec', 'snow', '初雪轻落，岁月安宁'],
  ['大雪', '#465a72', '#f2f5f8', '#d9e2ed', 'snow', '雪意渐深，围炉听曲'],
  ['冬至', '#68543f', '#faf6ef', '#eadfcd', 'bowl', '长夜将尽，团圆有暖'],
  ['小寒', '#4c5b71', '#f4f5f8', '#dfe2ed', 'blossom', '寒梅有信，静待春归'],
  ['大寒', '#655468', '#f8f4f7', '#e8dce6', 'snow', '岁寒将尽，新春可期'],
]
const festivalStyles = [
  ['春节', '#8b4034', '#fcf5eb', '#f0ddc3', 'lantern'],
  ['除夕', '#813e35', '#fbf4ec', '#eddbc9', 'lantern'],
  ['元宵节', '#88483b', '#fcf6ee', '#f0e0ce', 'lantern'],
  ['端午节', '#32644e', '#f4f8ed', '#dbe7c9', 'boat'],
  ['七夕', '#76516c', '#faf4f8', '#eadbe7', 'stars'],
  ['中秋节', '#74582e', '#fbf7ed', '#efe2bf', 'moon'],
  ['重阳节', '#6f5c32', '#faf7ee', '#ebe3c8', 'chrysanthemum'],
  ['腊八节', '#79533e', '#faf5ef', '#edddcd', 'bowl'],
  ['元旦', '#42636c', '#f4f8f7', '#d9e7e5', 'stars'],
  ['国庆节', '#8a4238', '#fcf5ed', '#efddca', 'cloud'],
]
const termThemes = new Map(termStyles.map(([name, accent, paper, wash, motif, headline], index) =>
  [name, { id: `term-${index + 1}`, name, accent, paper, wash, motif, headline, kind: 'term' }]))
const festivalThemes = new Map(festivalStyles.map(([name, accent, paper, wash, motif], index) =>
  [name, { id: `festival-${index + 1}`, name, accent, paper, wash, motif, headline: festivalGreetings.get(name), kind: 'festival' }]))

export function seasonalTheme(currentTerm, festivals = []) {
  const theme = festivalThemes.get(festivals[0]) || termThemes.get(currentTerm)
  if (!theme) throw new Error(`无法识别时令主题：${currentTerm}`)
  return {
    ...theme,
    tokens: {
      '--ink-accent': theme.accent,
      '--ink-paper': theme.paper,
      '--ink-wash': theme.wash,
      '--ink-surface': `${theme.paper}bf`,
      '--ink-reading': `${theme.paper}d9`,
      '--ink-header': `${theme.paper}f7`,
      '--ink-line': `${theme.accent}33`,
      '--ink-text': '#304039',
      '--ink-muted': '#4d5a52',
      '--season-ink': theme.accent,
      '--season-wash': theme.wash,
      '--season-hill': theme.wash,
      '--season-accent': theme.accent,
      '--primary': theme.accent,
      '--primary-light': theme.wash,
      '--bg': theme.paper,
    },
  }
}

export function chinaDateKey(date = new Date()) {
  const parts = Object.fromEntries(dateFormat.formatToParts(date).map(part => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day}`
}

const weeklyLayouts = [
  { id: 'mountains', name: '云山叠翠' },
  { id: 'bamboo', name: '竹影清风' },
  { id: 'river', name: '江舟远岫' },
  { id: 'lotus', name: '荷塘清韵' },
  { id: 'pines', name: '松风听泉' },
  { id: 'courtyard', name: '庭院听雨' },
  { id: 'moonlake', name: '平湖映月' },
]

export function seasonalCalendar(date = new Date()) {
  const dateKey = chinaDateKey(date)
  const [year, month, day] = dateKey.split('-').map(Number)
  const solar = Solar.fromYmd(year, month, day)
  const lunar = solar.getLunar()
  const termToday = lunar.getJieQi()
  const currentTerm = termToday || lunar.getPrevJieQi(true).getName()
  const nextTerm = lunar.getNextJieQi(true)
  const nextSolar = nextTerm.getSolar()
  const nextLunar = solar.next(1).getLunar()
  const festivals = [
    lunarFestivals.get(`${lunar.getMonth()}-${lunar.getDay()}`),
    lunar.getMonth() === 12 && nextLunar.getMonth() === 1 && nextLunar.getDay() === 1 ? '除夕' : null,
    solarFestivals.get(`${month}-${day}`),
  ].filter(Boolean)
  const season = seasons.find(item => item.terms.includes(currentTerm))
  if (!season) throw new Error(`无法识别节气：${currentTerm}`)
  const theme = seasonalTheme(currentTerm, festivals)
  const daysUntilNextTerm = Math.round((Date.UTC(nextSolar.getYear(), nextSolar.getMonth() - 1, nextSolar.getDay())
    - Date.UTC(year, month - 1, day)) / 86400000)
  return {
    dateKey, solarLabel: `${month}月${day}日`, weekday: `星期${solar.getWeekInChinese()}`,
    lunarLabel: `农历${lunar.getMonthInChinese()}月${lunar.getDayInChinese()}`,
    currentTerm, termToday, festivals, season: season.key, theme,
    layout: { ...weeklyLayouts[(solar.getWeek() + 6) % 7] },
    headline: theme.headline,
    description: festivals.length ? '把祝福留给牵挂的人，也留一份美好给自己。' : season.detail,
    nextTerm: nextTerm.getName(), daysUntilNextTerm,
  }
}
