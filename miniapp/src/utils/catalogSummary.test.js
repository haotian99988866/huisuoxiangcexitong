import test from 'node:test'
import assert from 'node:assert/strict'
import { movieSummary, musicSummary } from './catalogSummary.js'

test('movie totals distinguish the library, category and capped search results', () => {
  const data = { total: 1600, categories: [{ name: '电视剧', count: 300 }], series: Array(120).fill({}) }
  assert.equal(movieSummary(data, '', ''), '共 1600 部 · 当前展示 120 部')
  assert.equal(movieSummary(data, '电视剧', ''), '共 300 部 · 当前展示 120 部')
  assert.equal(movieSummary(data, '电视剧', '测试'), '当前展示 120 部')
  assert.equal(movieSummary(data, '未知分类', ''), '当前展示 120 部')
  assert.equal(movieSummary({ ...data, series: [] }, '', '不存在'), '当前展示 0 部')
})

test('music totals show accumulated visible tracks rather than page numbers', () => {
  assert.equal(musicSummary(8863, 20), '共 8863 首 · 已展示 20 首')
  assert.equal(musicSummary(23, 40), '共 23 首 · 已展示 23 首')
  assert.equal(musicSummary(0, 20), '共 0 首')
})

test('movie search totals report all matches with accumulated visible results', () => {
  assert.equal(movieSummary({ matching_total: 250, series: Array(48).fill({}) }, '', '测试'),
    '共 250 部 · 已展示 48 部')
})
