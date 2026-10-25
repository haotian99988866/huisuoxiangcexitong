import test from 'node:test'
import assert from 'node:assert/strict'
import { chinaDateKey, seasonalCalendar, seasonalTheme } from './seasonalCalendar.js'

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
