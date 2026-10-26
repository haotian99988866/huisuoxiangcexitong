import test from 'node:test'
import assert from 'node:assert/strict'
import { chinaDateKey, daylightTheme, seasonalCalendar, seasonalTheme } from './seasonalCalendar.js'

const day = value => seasonalCalendar(new Date(`${value}T12:00:00+08:00`))

test('calendar uses Beijing date rather than device timezone or Cambodia midnight', () => {
  assert.equal(chinaDateKey(new Date('2026-10-24T16:30:00Z')), '2026-10-25')
  assert.equal(chinaDateKey(new Date('2026-10-24T15:59:59Z')), '2026-10-24')
  const current = day('2026-10-25')
  assert.equal(current.lunarLabel, '农历九月十六')
  assert.equal(current.weekday, '星期日')
  assert.equal(current.currentTerm, '霜降')
  assert.equal(current.nextTerm, '立冬')
  assert.equal(current.daysUntilNextTerm, 13)
  assert.equal(current.season, 'autumn')
})

test('major traditional festivals and lunar new year eve use actual lunar dates', () => {
  for (const [date, festival] of [
    ['2026-02-16', '除夕'], ['2026-02-17', '春节'], ['2026-03-03', '元宵节'],
    ['2026-06-19', '端午节'], ['2026-08-19', '七夕'], ['2026-09-25', '中秋节'],
    ['2026-10-18', '重阳节'], ['2026-01-26', '腊八节'], ['2026-10-01', '国庆节'],
    ['2027-01-01', '元旦'], ['2022-01-31', '除夕'],
  ]) assert.ok(day(date).festivals.includes(festival), `${date} ${festival}`)
})

test('leap lunar months do not repeat regular lunar festivals', () => {
  const leap = day('2009-06-27')
  assert.equal(leap.lunarLabel, '农历闰五月初五')
  assert.ok(!leap.festivals.includes('端午节'))
  assert.ok(day('2009-05-28').festivals.includes('端午节'))
})

test('solar term day switches season for the full Beijing date, not the previous term', () => {
  for (const [date, term, season] of [
    ['2026-02-04', '立春', 'spring'], ['2026-05-05', '立夏', 'summer'],
    ['2026-08-07', '立秋', 'autumn'], ['2026-11-07', '立冬', 'winter'],
    ['2026-04-05', '清明', 'spring'], ['2026-12-22', '冬至', 'winter'],
  ]) {
    const result = day(date)
    assert.equal(result.termToday, term, date)
    assert.equal(result.currentTerm, term)
    assert.equal(result.season, season)
    assert.ok(result.daysUntilNextTerm > 0 && result.daysUntilNextTerm <= 16)
  }
  assert.equal(day('2026-02-03').season, 'winter')
})

test('every date of a full year resolves to a known season and a future solar term', () => {
  for (let offset = 0; offset < 365; offset++) {
    const date = new Date(Date.UTC(2026, 0, 1 + offset, 4))
    const result = seasonalCalendar(date)
    assert.ok(['spring', 'summer', 'autumn', 'winter'].includes(result.season), result.dateKey)
    assert.ok(result.daysUntilNextTerm > 0 && result.daysUntilNextTerm <= 16, result.dateKey)
  }
})

const luminance = hex => {
  const rgb = hex.slice(1, 7).match(/../g).map(channel => {
    const value = parseInt(channel, 16) / 255
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4
  })
  return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722
}
const contrast = (a, b) => {
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (values[0] + .05) / (values[1] + .05)
}

test('all 24 terms have distinct themes with readable buttons, text and decorative motifs', () => {
  const terms = new Map()
  for (let offset = 0; offset < 365; offset++) {
    const result = seasonalCalendar(new Date(Date.UTC(2026, 0, 1 + offset, 4)))
    terms.set(result.currentTerm, seasonalTheme(result.currentTerm))
    assert.equal(result.theme.name, result.festivals[0] || result.currentTerm)
    assert.equal(result.theme.kind, result.festivals.length ? 'festival' : 'term')
  }
  assert.equal(terms.size, 24)
  assert.equal(new Set([...terms.values()].map(theme => theme.id)).size, 24)
  assert.equal(new Set([...terms.values()].map(theme => theme.accent)).size, 24)
  for (const theme of terms.values()) assertReadableTheme(theme)
})

function assertReadableTheme(theme) {
  assert.match(theme.motif, /^(blossom|rain|leaf|lotus|wheat|sun|reed|chrysanthemum|snow|lantern|moon|stars|boat|bowl|cloud)$/)
  assert.ok(contrast(theme.accent, '#fffef5') >= 4.5, `${theme.name}: button label contrast`)
  for (const background of [theme.paper, theme.wash]) {
    for (const foreground of [theme.accent, theme.tokens['--ink-text'], theme.tokens['--ink-muted']]) {
      assert.ok(contrast(foreground, background) >= 4.5, `${theme.name}: ${foreground} on ${background}`)
    }
  }
  assert.equal(theme.tokens['--ink-surface'], `${theme.paper}bf`)
  assert.equal(theme.tokens['--ink-reading'], `${theme.paper}d9`)
  assert.equal(theme.tokens['--ink-header'], `${theme.paper}f7`)
}

test('ten festival themes override solar terms only on the actual festival date', () => {
  const themes = new Set()
  for (const [date, festival] of [
    ['2026-02-16', '除夕'], ['2026-02-17', '春节'], ['2026-03-03', '元宵节'],
    ['2026-06-19', '端午节'], ['2026-08-19', '七夕'], ['2026-09-25', '中秋节'],
    ['2026-10-18', '重阳节'], ['2026-01-26', '腊八节'], ['2026-10-01', '国庆节'],
    ['2027-01-01', '元旦'],
  ]) {
    const theme = day(date).theme
    assert.equal(theme.name, festival)
    assert.equal(theme.kind, 'festival')
    themes.add(theme.id)
    assertReadableTheme(theme)
  }
  assert.equal(themes.size, 10)
  assert.equal(day('2026-09-26').theme.name, '秋分')
  assert.equal(day('2026-02-18').theme.name, '雨水')
  assert.equal(day('2020-10-01').theme.name, '中秋节')
  assert.equal(day('2009-06-27').theme.kind, 'term')
})

test('theme follows Beijing midnight, term persistence and next-term rollover', () => {
  assert.equal(seasonalCalendar(new Date('2026-09-24T15:59:59Z')).theme.name, '秋分')
  assert.equal(seasonalCalendar(new Date('2026-09-24T16:00:00Z')).theme.name, '中秋节')
  assert.equal(seasonalCalendar(new Date('2026-09-25T16:00:00Z')).theme.name, '秋分')
  assert.equal(day('2026-10-24').theme.name, '霜降')
  assert.equal(day('2026-11-06').theme.name, '霜降')
  assert.equal(day('2026-11-07').theme.name, '立冬')
  assert.throws(() => seasonalTheme('未知节气'), /无法识别时令主题/)
})

test('seven ink compositions rotate Monday to Sunday and repeat on the next week', () => {
  const expected = [
    ['mountains', '云山叠翠'], ['bamboo', '竹影清风'], ['river', '江舟远岫'],
    ['lotus', '荷塘清韵'], ['pines', '松风听泉'], ['courtyard', '庭院听雨'],
    ['moonlake', '平湖映月'],
  ]
  for (let offset = 0; offset < 14; offset++) {
    const result = seasonalCalendar(new Date(Date.UTC(2026, 9, 19 + offset, 4)))
    assert.deepEqual(result.layout, { id: expected[offset % 7][0], name: expected[offset % 7][1] })
  }
  assert.equal(day('2026-10-25').layout.id, 'moonlake')
  assert.equal(day('2026-10-26').layout.id, 'mountains')
})

test('weekly composition uses Beijing midnight and survives month/year and festival transitions', () => {
  const before = seasonalCalendar(new Date('2026-10-25T15:59:59Z'))
  const after = seasonalCalendar(new Date('2026-10-25T16:00:00Z'))
  assert.equal(before.layout.id, 'moonlake')
  assert.equal(after.layout.id, 'mountains')
  assert.equal(before.theme.id, after.theme.id)
  assert.equal(day('2026-12-31').layout.id, 'lotus')
  assert.equal(day('2027-01-01').layout.id, 'pines')
  assert.equal(day('2027-01-01').theme.name, '元旦')
  assert.equal(day('2026-09-25').layout.id, 'pines')
  assert.equal(day('2026-09-25').theme.name, '中秋节')
  assert.equal(day('2026-09-26').layout.id, 'courtyard')
  assert.equal(day('2026-09-26').theme.name, '秋分')
  const seen = new Set()
  for (let offset = 0; offset < 365; offset++) {
    const date = new Date(Date.UTC(2026, 0, 1 + offset, 4))
    const result = seasonalCalendar(date)
    seen.add(result.layout.id)
    assert.equal(result.layout.id, [
      'moonlake', 'mountains', 'bamboo', 'river', 'lotus', 'pines', 'courtyard',
    ][date.getUTCDay()])
  }
  assert.equal(seen.size, 7)
})

test('daylight uses Cambodia time independently of Beijing date and retains seasonal identity', () => {
  const date = new Date('2026-10-25T16:30:00Z')
  const current = seasonalCalendar(date)
  assert.equal(current.dateKey, '2026-10-26')
  assert.equal(current.layout.id, 'mountains')
  assert.equal(current.theme.daylight.hour, 23.5)
  assert.equal(current.theme.daylight.timeZone, 'Asia/Phnom_Penh')
  const base = seasonalTheme('霜降')
  for (const hour of [0, 5, 8, 12, 17.5, 21, 23.5]) {
    const result = daylightTheme(base, new Date(Date.UTC(2026, 9, 26, 0, hour * 60 - 420)))
    assert.equal(result.id, base.id)
    assert.equal(result.motif, base.motif)
    assert.equal(result.kind, base.kind)
    assert.equal(result.name, base.name)
    assert.equal(result.daylight.hour, hour)
  }
  const colors = hour => daylightTheme(base, new Date(`2026-10-26T${hour}:00:00+07:00`))
  assert.ok(luminance(colors('00').paper) < luminance(colors('12').paper))
  assert.notEqual(colors('08').accent, colors('12').accent)
  assert.notEqual(colors('17').accent, colors('21').accent)
  assert.deepEqual(base, seasonalTheme('霜降'), 'base palette remains unchanged')
})

test('all seasonal and festival palettes stay readable throughout the Cambodia day', () => {
  const bases = new Map()
  for (let offset = 0; offset < 365; offset++) {
    const current = seasonalCalendar(new Date(Date.UTC(2026, 0, 1 + offset, 4)))
    bases.set(current.theme.id, seasonalTheme(current.currentTerm, current.festivals))
  }
  assert.equal(bases.size, 34)
  for (const base of bases.values()) {
    for (let minute = 0; minute < 1440; minute++) {
      const theme = daylightTheme(base, new Date(Date.UTC(2026, 9, 26, 0, minute - 420)))
      assertReadableTheme(theme)
    }
  }
})

test('daylight interpolation is continuous at stops and Cambodia midnight', () => {
  const base = seasonalTheme('霜降')
  const distance = (a, b) => Math.max(...['accent', 'paper', 'wash'].flatMap(key => {
    const channels = color => color.slice(1).match(/../g).map(channel => parseInt(channel, 16))
    const target = channels(b[key])
    return channels(a[key]).map((value, index) => Math.abs(value - target[index]))
  }))
  for (const minute of [0, 300, 480, 720, 1050, 1260, 1440]) {
    const at = Date.UTC(2026, 9, 26, 0, minute - 420)
    assert.ok(distance(daylightTheme(base, new Date(at - 1000)), daylightTheme(base, new Date(at))) <= 1)
    assert.ok(distance(daylightTheme(base, new Date(at)), daylightTheme(base, new Date(at + 1000))) <= 1)
  }
  const first = seasonalCalendar(new Date('2026-10-26T01:00:00Z'))
  const sameMinute = seasonalCalendar(new Date('2026-10-26T01:00:59Z'))
  const later = seasonalCalendar(new Date('2026-10-26T02:00:00Z'))
  assert.equal(first.appearanceKey, sameMinute.appearanceKey)
  assert.notEqual(first.appearanceKey, later.appearanceKey)
  assert.notEqual(first.theme.paper, later.theme.paper)
  assert.equal(first.dateKey, later.dateKey)
})
