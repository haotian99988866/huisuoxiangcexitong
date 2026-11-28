import test from 'node:test'
import assert from 'node:assert/strict'
import { deliveryAttendanceNotice, staffAttendanceNotice } from './attendanceNotice.js'

const staff = {
  photoFirst: true, attendanceOnly: true, visibleCount: 0, searchTerm: '',
  boardFilter: '全部', rosterAvailable: false, hasDutyCodes: false,
  pendingPhotos: 0, rosterDutyOnly: false, shiftName: '晚班',
}

test('only unsynced attendance gets the staff notice', () => {
  assert.deepEqual(staffAttendanceNotice(staff), {
    title: '晚班出勤尚未同步',
  })
  for (const change of [
    { visibleCount: 1 }, { searchTerm: '1' }, { boardFilter: '1号牌' },
    { attendanceOnly: false }, { photoFirst: false },
  ]) assert.equal(staffAttendanceNotice({ ...staff, ...change }), null)
})

test('staff notice does not call a published roster unsynced', () => {
  for (const change of [{ rosterAvailable: true }, { hasDutyCodes: true }]) {
    assert.match(staffAttendanceNotice({ ...staff, ...change }).title, /暂无可展示出勤档案/)
  }
})

test('delivery notice distinguishes daytime and empty evening without claiming publication status', () => {
  assert.match(deliveryAttendanceNotice({ photoFirst: true, attendanceOnly: true, visibleCount: 0, shift: '白班' }).title, /只展示晚班/)
  assert.match(deliveryAttendanceNotice({ photoFirst: true, attendanceOnly: true, visibleCount: 0, shift: '晚班' }).title, /暂无可展示/)
  assert.equal(deliveryAttendanceNotice({ photoFirst: true, attendanceOnly: true, visibleCount: 1 }), null)
  assert.equal(deliveryAttendanceNotice({ photoFirst: true, attendanceOnly: false, visibleCount: 0 }), null)
})
