import test from 'node:test'
import assert from 'node:assert/strict'
import { readSheet } from 'read-excel-file/node'
import writeExcelFile from 'write-excel-file/node'
import { importedPrices, priceSheetRows } from './priceSpreadsheet.js'

const items = [
  { venue_id: 2, service_key: 'member', service_name: '名玺牌·会员', price: '599.00' },
  { venue_id: 2, service_key: 'half', service_name: '名玺牌·半价', price: null },
  { venue_id: 4, service_key: 'club', service_name: '5牌', price: '99.00' },
]

test('real Excel round trip imports only entered prices for the selected venue', async () => {
  const rows = priceSheetRows(items.slice(0, 2))
  rows[2][1] = 299.5
  const buffer = await writeExcelFile(rows).toBuffer()
  const decoded = await readSheet(buffer)
  assert.deepEqual(importedPrices(decoded, items, '2'), { '2:member': '599', '2:half': '299.5' })
  decoded[1][1] = null
  assert.deepEqual(importedPrices(decoded, items, '2'), { '2:half': '299.5' })
})

test('invalid prices and mismatched or duplicate projects reject the entire import', () => {
  const rows = priceSheetRows(items.slice(0, 2))
  for (const badPrice of [0, -1, 1.234, '1e2', true]) {
    const invalid = structuredClone(rows)
    invalid[1][1] = badPrice
    assert.throws(() => importedPrices(invalid, items, '2'), /价格/)
  }
  assert.throws(() => importedPrices(rows, items, '4'), /不匹配/)
  const renamed = structuredClone(rows)
  renamed[1][0] = '其他项目'
  assert.throws(() => importedPrices(renamed, items, '2'), /不匹配/)
  assert.throws(() => importedPrices([...rows, rows[1]], items, '2'), /不匹配/)
  assert.throws(() => importedPrices([['错误表头']], items, '2'), /格式/)
})
