const paths = {
  venue: ['m3 10 9-7 9 7', 'M5 9v11h14V9', 'M9 20v-7h6v7'],
  delivery: ['M3 6h10v11H3Z', 'M13 10h4l4 4v3h-8', 'M7 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z', 'M17 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z'],
  movies: ['M3 8h18v12H3Z', 'm3 8 17-5 1 5', 'm7 7-2-2m7 0-2-2m7 1-2-2', 'm10 12 5 3-5 3Z'],
  music: ['M9 17V5l11-2v12', 'M9 8l11-2', 'M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z', 'M17 19a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z'],
  announcements: ['M4 9h5l10-5v16L9 15H4Z', 'm6 15 2 6h3l-2-6', 'M22 9v6'],
  recharge: ['M3 5h18v14H3Z', 'M3 9h18', 'M7 15h4'],
  personal: ['M16 7a4 4 0 1 0-8 0 4 4 0 0 0 8 0Z', 'M4 21v-2a8 8 0 0 1 16 0v2'],
  contact: ['M21 11a9 9 0 0 1-9 9H4l-2 2V11a9 9 0 1 1 19 0Z', 'M7 11h.01M12 11h.01M17 11h.01'],
  waiting: ['M6 3h12M6 21h12', 'M7 3v4l5 5-5 5v4M17 3v4l-5 5 5 5v4'],
  daylight: ['M16 12a4 4 0 1 0-8 0 4 4 0 0 0 8 0Z', 'M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5'],
  search: ['M17 10a7 7 0 1 0-14 0 7 7 0 0 0 14 0Z', 'm15 15 6 6'],
  photo: ['M3 4h18v16H3Z', 'm3 16 5-5 4 4 3-3 6 6', 'M16 8h.01'],
  clock: ['M21 12a9 9 0 1 0-18 0 9 9 0 0 0 18 0Z', 'M12 7v5l3 2'],
  location: ['M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z', 'M15 10a3 3 0 1 0-6 0 3 3 0 0 0 6 0Z'],
  phone: ['M5 3h4l2 5-3 2a15 15 0 0 0 6 6l2-3 5 2v4q0 3-3 2C9 20 4 15 3 6q-1-3 2-3Z'],
}

export default function ModuleIcon({ name }) {
  return <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {(paths[name] || ['M4 4h16v16H4Z']).map((path, index) => <path key={index} d={path} />)}
  </svg>
}
