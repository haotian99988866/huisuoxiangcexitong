/** 列表加载骨架：比例与列数对齐正式列表，减少加载完成时跳动。 */
export default function PhotoSkeleton({
  count = 6,
  columns = 2,
  label = '正在读取…',
  dense = false,
  chips = false,
  hero = false,
}) {
  if (hero) {
    return (
      <div className="photo-skeleton photo-skeleton--hero" role="status" aria-label={label}>
        <div className="photo-skeleton__hero" />
        <div className="photo-skeleton__thumb-row">
          {Array.from({ length: 4 }, (_, index) => (
            <div key={index} className="photo-skeleton__thumb" />
          ))}
        </div>
        <p className="photo-skeleton__label photo-skeleton__label--inline">{label}</p>
      </div>
    )
  }

  return (
    <div className={`photo-skeleton${dense ? ' photo-skeleton--dense' : ''}`} role="status" aria-label={label}>
      {label ? <p className="photo-skeleton__label">{label}</p> : null}
      {chips && (
        <div className="photo-skeleton__chips" aria-hidden="true">
          {Array.from({ length: 5 }, (_, index) => (
            <div key={index} className="photo-skeleton__chip" />
          ))}
        </div>
      )}
      <div
        className="photo-skeleton__grid"
        style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      >
        {Array.from({ length: count }, (_, index) => (
          <div key={index} className="photo-skeleton__card">
            <div className="photo-skeleton__cover" />
            <div className="photo-skeleton__line" />
            <div className="photo-skeleton__line photo-skeleton__line--short" />
          </div>
        ))}
      </div>
    </div>
  )
}
