import test from 'node:test'
import assert from 'node:assert/strict'
import { financeCategoryAnalysis } from './financeAnalysis.js'

test('commission-only income appears even when there are no category entries', () => {
  const [expense, income] = financeCategoryAnalysis({
    budgets: [], summary: { commission: '160.00' },
  })
  assert.equal(expense.total, 0)
  assert.equal(income.total, 160)
  assert.equal(income.rows[0].id, 'commission')
})

test('commission is added once alongside category totals without mutating input', () => {
  const data = {
    budgets: [
      { id: 1, kind: 'income', name: '其他收入', spent: '20.00' },
      { id: 2, kind: 'expense', name: '餐饮', spent: '12.50' },
    ],
    summary: { commission: '160.00' },
  }
  const [expense, income] = financeCategoryAnalysis(data)
  assert.equal(income.total, 180)
  assert.equal(expense.total, 12.5)
  assert.deepEqual(income.rows.map(row => row.id), ['commission', 1])
  assert.equal(data.budgets.length, 2)
})

test('empty data and zero commission keep empty categories', () => {
  assert.deepEqual(financeCategoryAnalysis().map(group => group.rows), [[], []])
  assert.equal(financeCategoryAnalysis({ budgets: [], summary: { commission: '0' } })[1].total, 0)
})
