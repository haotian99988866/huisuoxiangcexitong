import { useId } from 'react'

export default function InkScenery({ className = '', compact = false }) {
  const id = useId().replace(/:/g, '')
  return <svg className={`ink-scenery${compact ? ' is-compact' : ''} ${className}`} viewBox={compact ? '0 300 600 540' : '0 0 600 900'}
    preserveAspectRatio="xMidYMax slice" aria-hidden="true" focusable="false">
    <defs>
      <linearGradient id={`${id}-wash`} x2="0" y2="1">
        <stop stopColor="var(--ink-accent)" stopOpacity=".22" />
        <stop offset="1" stopColor="var(--ink-accent)" stopOpacity=".03" />
      </linearGradient>
    </defs>
    <g className="ink-scene ink-scene-mountains">
      <circle cx="466" cy="138" r="34" className="ink-scene-disc" />
      <g fill={`url(#${id}-wash)`}>
        <path d="M-30 315Q13 246 39 227T83 151Q91 129 102 153L124 197Q145 204 162 249L201 321Q228 349 241 390H-30Z" />
        <path d="M280 410Q318 332 345 314L381 228Q396 198 413 237L435 271Q456 253 472 282L503 338Q545 301 563 256L591 202Q608 177 631 231V475H280Z" />
        <path d="M350 692q60-49 103-63 22-56 41-82 13-17 24 6l24 42q28 5 66 44v142H350Z" />
      </g>
      <path className="ink-scene-solid" d="M-40 427Q8 351 26 317L59 268Q69 253 82 279L105 326Q129 331 151 368L185 421 235 469H-40Z" />
      <path className="ink-scene-line" d="M60 606q110-13 217 0m-84 27q151-12 270-1M12 747q95-7 166-2m37 25q143-12 300-2" />
      <path className="ink-scene-detail" d="m290 720 62 0-12 10h-40Zm29-4v-51l26 46h-26Z" />
    </g>
    <g className="ink-scene ink-scene-bamboo">
      <path fill={`url(#${id}-wash)`} d="M-20 650q160-170 282-50 170-150 358 20v220H-20Z" />
      <g className="ink-scene-line ink-scene-bamboo-stems">
        <path d="M77 850 100 120m-6 190 65-82M84 580l-58-75M160 855l-12-440m-1 90 58-65M92 345l24 1M83 580l23 1m-28 95 23 1m44-138h23m-23 116h23" />
      </g>
      <path className="ink-scene-detail" d="M101 313q-54-69-67-36 20 30 67 36Zm34-51q27-76 46-47-4 32-46 47ZM87 565q-61-12-55-39 30-10 55 39Zm75-70q51-62 59-30-15 27-59 30Zm-4 157q-43-48-53-22 13 24 53 22Z" />
      <circle cx="456" cy="440" r="42" className="ink-scene-disc" />
      <path className="ink-scene-line" d="M270 710h230m-200 30h150m-60 38h170" />
    </g>
    <g className="ink-scene ink-scene-river">
      <path fill={`url(#${id}-wash)`} d="M-20 265 53 161q14-18 25 5l92 125 37-45 86 108-96 92H-20Zm640 130-68-128q-14-24-32 3l-80 115-66-35-78 90 93 75h231Z" />
      <path className="ink-scene-solid" d="M-10 520q175-129 280-30T610 510v65q-170 83-314-9T-10 600Z" />
      <path className="ink-scene-line" d="M70 650q130-25 258 0m-151 48q154-26 345-5M30 769q121-18 247 0m79 29h166" />
      <path className="ink-scene-detail" d="m238 697 100 0-18 16h-65Zm44-5v-83l43 73h-43Z" />
      <path className="ink-scene-line" d="M405 176q12-14 26 0 12-14 25 0m-91 30q8-10 16 0 8-10 16 0" />
    </g>
    <g className="ink-scene ink-scene-lotus">
      <path fill={`url(#${id}-wash)`} d="M-20 530q150-67 300 0t340-20v85q-170-43-320 9T-20 590Z" />
      <g className="ink-scene-line">
        <path d="M446 807q-24-100 3-223m-4 115q-77-64-108-17 20 49 108 17Zm4-115q-64-21-46-63 28 7 46 63Zm0 0q64-21 46-63-28 7-46 63Zm0 0q-27-52 0-83 27 31 0 83ZM120 824q-8-88 23-134m0 0q-73 31-97-4 37-44 97 4Zm0 0q20-55 68-28-10 38-68 28ZM28 786h230m46 47h248M77 620h144" />
      </g>
      <ellipse className="ink-scene-solid" cx="300" cy="745" rx="63" ry="14" />
      <circle className="ink-scene-disc" cx="112" cy="230" r="38" />
    </g>
    <g className="ink-scene ink-scene-pines">
      <path fill={`url(#${id}-wash)`} d="M-20 650 70 521l58 43 75-140 78 153 57-61 122 161 60-82 100 94v151H-20Z" />
      <path className="ink-scene-line ink-scene-tree" d="M490 835q-19-177-5-363m-3 104-69-62m71 131 73-69m-74 153-85-62" />
      <path className="ink-scene-detail" d="m414 466-67 72h135Zm72-46-61 83h122Zm72 111-74 74h142Zm-162 83-74 75h151Z" />
      <circle className="ink-scene-disc" cx="175" cy="275" r="48" />
      <path className="ink-scene-line" d="M32 795h223m-178 29h168" />
    </g>
    <g className="ink-scene ink-scene-courtyard">
      <path fill={`url(#${id}-wash)`} d="M-20 422q130-105 248 0t392-25v109q-130-62-273-5T-20 510Z" />
      <path className="ink-scene-solid" d="M133 667q80-13 137-75 64 61 145 75l-24 18H155Z" />
      <g className="ink-scene-line">
        <path d="M162 687v106m219-106v106m-165-98v86m110-86v86m-173 13h240M195 812h156M46 821q27-150 49-208m-8 34-46-26m35 63 53-27m-70 91-37-15M431 757q43-25 101 0m-91 25h114" />
      </g>
      <path className="ink-scene-detail" d="m54 618 15-38 9 40 33-16-15 32-42-18Zm16 74 49-16-19 30-30-14Z" />
      <circle className="ink-scene-disc" cx="448" cy="265" r="38" />
    </g>
    <g className="ink-scene ink-scene-moonlake">
      <circle className="ink-scene-disc" cx="179" cy="205" r="67" />
      <path fill={`url(#${id}-wash)`} d="M-20 525 47 441l60 28 54-72 90 132 82-57 68 35 94-123 125 158v71H-20Z" />
      <path className="ink-scene-line" d="M50 642h191m-148 30h171m-113 29h59m100 51h233m-173 33h115M440 840q15-103 64-171m-42 89-27-49m44 17 43-10m-22-39 34 2" />
      <path className="ink-scene-detail" d="m245 762 73 0-13 12h-47Zm31-6v-46l24 40h-24Z" />
      <path className="ink-scene-solid" d="M-20 830q105-80 199-20l43 30H-20Z" />
    </g>
  </svg>
}
