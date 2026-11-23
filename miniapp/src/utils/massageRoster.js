export function massageRosterMembers(members) {
  return new Map(members.map(member => [String(member.code), member]))
}

export function massageRosterPhoto(code, members) {
  const member = members.get(String(code))
  if (member?.cover) return { id: member.cover, kind: 'staff-photos', count: member.photo_count }
  return null
}

export function massageRosterServices(code, items) {
  return items.filter(item => item.technician_codes?.split(',')
    .some(value => value.trim() === String(code)))
}
