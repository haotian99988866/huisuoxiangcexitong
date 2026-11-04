export function appendUnique(previous, incoming, key) {
  const seen = new Set(previous.map(key))
  return [...previous, ...incoming.filter(item => {
    const identity = key(item)
    if (seen.has(identity)) return false
    seen.add(identity)
    return true
  })]
}

export function pullDistance(start, point) {
  const vertical = point.y - start.y
  return vertical > 0 && vertical > Math.abs(point.x - start.x) * 1.5
    ? Math.min(110, vertical * 0.5) : 0
}
