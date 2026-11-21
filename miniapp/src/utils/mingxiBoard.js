/** 名玺牌分层：与频道全员名单 / 价目表对齐 */

const JP_KR_CODES = new Set(['01', '02', '03', '05', '06', '07', '08', '77'])
const MINGXI_PLATE_CODES = new Set(['3333', '5555', '7777', '8888', '9999'])
const BOARD_ALIAS = {
  日韩专场: '日韩牌',
  日韩牌: '日韩牌',
  俄罗斯: '7号牌',
}

export const MINGXI_BOARD_ORDER = [
  '名玺牌',
  '日韩牌',
  '9号牌',
  '8号牌',
  '7号牌',
  '6号牌',
  '5号牌',
  '3号牌',
  '2号牌',
  '1号牌',
  '其他',
]

export function normalizeMingxiBoard(name) {
  const value = String(name || '').trim()
  if (!value) return '其他'
  return BOARD_ALIAS[value] || value
}

export function classifyMingxiBoard(code) {
  const value = String(code || '').trim()
  if (MINGXI_PLATE_CODES.has(value)) return '名玺牌'
  if (JP_KR_CODES.has(value) || (/^\d{1,2}$/.test(value))) return '日韩牌'
  if (value === '7026' || (/^\d{3,}$/.test(value) && value[0] === '7')) return '7号牌'
  if (/^\d{3,}$/.test(value)) return `${value[0]}号牌`
  return '其他'
}

export function memberMingxiBoard(member, codeBoardMap) {
  const fromPrice = member?.price_options?.[0]?.board
  if (fromPrice) return normalizeMingxiBoard(fromPrice)
  const mapped = codeBoardMap?.get(String(member?.code))
  if (mapped) return mapped
  return classifyMingxiBoard(member?.code)
}

/** 从出勤初稿 roster 分组建 工号→牌 */
export function buildCodeBoardMap(attendanceGroups) {
  const map = new Map()
  for (const group of attendanceGroups || []) {
    if (group.group_type && group.group_type !== 'roster') continue
    const board = normalizeMingxiBoard(group.group_name)
    for (const code of group.technician_codes || []) {
      const key = String(code).trim()
      if (key && !map.has(key)) map.set(key, board)
    }
  }
  return map
}

export function boardSortIndex(board) {
  const index = MINGXI_BOARD_ORDER.indexOf(board)
  return index >= 0 ? index : MINGXI_BOARD_ORDER.length
}

export function displayMingxiPrice(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return ''
  if (Number.isInteger(amount)) return String(amount)
  return amount.toFixed(2).replace(/\.?0+$/, '')
}
