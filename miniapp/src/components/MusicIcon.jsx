const paths = {
  play: 'm9 5 11 7-11 7Z',
  pause: 'M8 5v14M16 5v14',
  next: 'm5 5 10 7-10 7ZM19 5v14',
  previous: 'm19 5-10 7 10 7ZM5 5v14',
  heart: 'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  plus: 'M12 5v14M5 12h14',
  close: 'm6 6 12 12M6 18 18 6',
  back: 'm15 18-6-6 6-6M9 12h11',
  search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  refresh: 'M20 7V3l-4 4M4 17v4l4-4M20 7a9 9 0 0 0-15-2M4 17a9 9 0 0 0 15 2',
  note: 'M9 18V5l11-2v13M9 8l11-2M9 18c0 4-6 4-6 1s6-4 6-1M20 16c0 4-6 4-6 1s6-4 6-1',
  share: 'M12 16V3m-4 4 4-4 4 4M5 13v7h14v-7',
  list: 'M9 6h12M9 12h12M9 18h12M3 6h.01M3 12h.01M3 18h.01',
  down: 'm5 9 7 7 7-7',
  check: 'm5 12 4 4L19 6',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  eyeOff: 'm3 3 18 18M10 5.2A12 12 0 0 1 12 5c6 0 10 7 10 7a21 21 0 0 1-3 3.8M6.3 6.3A23 23 0 0 0 2 12s4 7 10 7a13 13 0 0 0 5.7-1.3M10 10a3 3 0 0 0 4 4',
}

export default function MusicIcon({ name, size = 20 }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={name === 'more' ? 4 : 1.8} strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.note} /></svg>
}
