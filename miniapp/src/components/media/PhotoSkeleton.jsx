/** 列表加载骨架：固定比例灰块，避免「正在读取…」空白页。 */
export default function PhotoSkeleton({ count = 4, columns = 2, label = '正在读取…' }) {
  return (
    <div className="photo-skeleton" role="status" aria-label={label}>
      <p className="photo-skeleton__label">{label}</p>
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
