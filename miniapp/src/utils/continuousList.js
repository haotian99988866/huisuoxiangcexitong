export function appendUnique(previous, incoming, key) {
  const seen = new Set(previous.map(key))
  return [...previous, ...incoming.filter(item => {
    const identity = key(item)
    if (seen.has(identity)) return false
    seen.add(identity)
    return true
  })]
}
