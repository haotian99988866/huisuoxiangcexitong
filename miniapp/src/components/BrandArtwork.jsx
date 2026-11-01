import { useId } from 'react'

export default function BrandArtwork() {
  const gradient = useId()
  return <svg className="brand-artwork" viewBox="0 0 260 260" aria-hidden="true" focusable="false">
    <defs><linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
      <stop stopColor="#e7c990" /><stop offset="1" stopColor="#b9c6d9" />
    </linearGradient></defs>
    <circle cx="155" cy="120" r="84" fill={`url(#${gradient})`} opacity=".24" />
    <g fill="none" stroke={`url(#${gradient})`} strokeWidth="2">
      <ellipse cx="155" cy="120" rx="113" ry="52" transform="rotate(-35 155 120)" />
      <ellipse cx="155" cy="120" rx="98" ry="72" transform="rotate(35 155 120)" />
      <path d="m155 70 14 31 34 4-25 24 6 35-29-17-30 17 6-35-25-24 35-4Z" />
    </g>
    <circle cx="65" cy="176" r="7" fill="#d8bb8c" opacity=".6" />
    <circle cx="220" cy="58" r="4" fill="#b8c6d9" />
  </svg>
}
