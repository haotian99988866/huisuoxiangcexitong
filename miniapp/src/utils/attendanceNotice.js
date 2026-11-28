export function staffAttendanceNotice({
  photoFirst, attendanceOnly, visibleCount, searchTerm, boardFilter,
  rosterAvailable, hasDutyCodes, shiftName,
}) {
  if (!photoFirst || !attendanceOnly || visibleCount || searchTerm || boardFilter !== '全部') return null

  return {
    title: !rosterAvailable && !hasDutyCodes
      ? `${shiftName}出勤尚未同步`
      : `${shiftName}暂无可展示出勤档案`,
  }
}

export function deliveryAttendanceNotice({ photoFirst, attendanceOnly, visibleCount, shift }) {
  if (!photoFirst || !attendanceOnly || visibleCount) return null
  return shift === '白班'
    ? { title: '西港外送只展示晚班出勤', description: '当前为白班时间，晚班出勤将在此展示。' }
    : { title: '晚班出勤暂无可展示档案', description: '当前没有可展示的晚班出勤档案。' }
}
