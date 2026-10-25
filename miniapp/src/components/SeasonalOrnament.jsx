export default function SeasonalOrnament() {
  return <svg className="seasonal-ornament" viewBox="0 0 180 180" aria-hidden="true" focusable="false">
    <g className="ornament-blossom">
      <path d="m170 15-70 90m37-48-47-9m33 29 7 43" />
      {[ [137, 49], [99, 91], [89, 46], [129, 120] ].map(([cx, cy]) =>
        <g key={cx} transform={`translate(${cx} ${cy})`}>
          <path d="M0 0C-29-6-14-29 0-9 14-29 29-6 9 0 29 14 6 29 0 9-6 29-29 14-9 0Z" />
        </g>)}
    </g>
    <g className="ornament-rain"><path d="M40 45q15-24 34-4 23-31 40-1 28-8 28 17H37q-11-7 3-12Zm12 41-7 24m38-24-7 24m38-24-7 24m-43 12-7 22m38-22-7 22" /></g>
    <g className="ornament-leaf"><path d="M153 25Q101 71 50 147m69-83q-48-2-42-31 37-7 42 31Zm-24 30q16-43 45-31-1 33-45 31Zm-24 26q-39-6-32-31 30-6 32 31Z" /></g>
    <g className="ornament-lotus"><path d="M90 103Q32 90 47 54q26 5 43 49Zm0 0q58-13 43-49-26 5-43 49Zm0 0Q63 62 90 31q27 31 0 72Zm-42 27q42-19 84 0M90 103v31M30 145h120" /></g>
    <g className="ornament-wheat"><path d="M62 147 111 28m-39 94q-34-8-27-29 26 1 27 29Zm10-25q31 15 39-8-19-17-39 8Zm10-24q-29-10-23-30 25 3 23 30Zm10-24q24 7 28-13-15-13-28 13Z" /></g>
    <g className="ornament-sun"><circle cx="90" cy="76" r="28" /><path d="M90 23v-12m0 118v-12m53-41h12M25 76h12m15-38-9-9m85 85 9 9m-9-85 9-9m-85 85-9 9M40 149h100" /></g>
    <g className="ornament-reed"><path d="m50 154 47-115m-19 116 50-98m-66 66-32-17m46-19-30-15m39-23-26-14m48 53 27-6m-18-24 28-6M97 39l-4-18m35 36 9-17" /></g>
    <g className="ornament-chrysanthemum"><circle cx="90" cy="75" r="12" />{Array.from({length: 12}, (_, i) =>
      <ellipse key={i} cx="90" cy="47" rx="7" ry="17" transform={`rotate(${i * 30} 90 75)`} />)}<path d="M90 116v39m0-20q-28-26-36-12 4 17 36 12" /></g>
    <g className="ornament-snow"><path d="M90 25v130M34 57l112 66M34 123l112-66M78 38l12 12 12-12M78 142l12-12 12 12M39 72l18-3-3-18m72 90-3-18 18-3M39 108l18 3-3 18m72-90-3 18 18 3" /></g>
    <g className="ornament-lantern"><path d="M90 8v28m-20 0h40v8H70Zm0 84h40v8H70Zm20 8v31m-12 11h24" /><ellipse cx="90" cy="68" rx="36" ry="27" /><ellipse cx="90" cy="68" rx="16" ry="27" /></g>
    <g className="ornament-moon"><circle cx="99" cy="69" r="39" /><path d="M111 33q-42 35-3 74M26 132h86m-63 12h104m-17-37h30" /></g>
    <g className="ornament-stars"><path d="m61 36 6 19 20 1-16 12 5 20-16-12-17 12 6-20-16-12 20-1Zm65 32 4 13 14 1-11 8 3 14-11-8-11 8 4-14-11-8 14-1ZM34 131q53-55 114 0m-101 11q42-41 88 0" /></g>
    <g className="ornament-boat"><path d="m28 107 124 0-24 27H54ZM90 38v69m0-68-41 56h41m15-44 25 44h-25M30 147h120" /></g>
    <g className="ornament-bowl"><path d="M40 94h100q-4 46-50 46T40 94Zm-7-7h114M71 64q-13-12 0-25m23 25q-13-12 0-25m23 25q-13-12 0-25M68 150h44" /></g>
    <g className="ornament-cloud"><path d="M28 92q-3-27 26-24 7-38 35-15 28-36 46 8 32-1 21 31H28Zm6 27h111m-93 19h74" /></g>
  </svg>
}
