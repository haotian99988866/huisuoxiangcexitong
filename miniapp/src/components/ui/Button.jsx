const VARIANTS = {
  primary: 'ui-btn ui-btn--primary',
  ghost: 'ui-btn ui-btn--ghost',
  quiet: 'ui-btn ui-btn--quiet',
  danger: 'ui-btn ui-btn--danger',
}

export function buttonClass(variant = 'primary', extra = '') {
  return [VARIANTS[variant] || VARIANTS.primary, extra].filter(Boolean).join(' ')
}

export function Button({
  variant = 'primary',
  type = 'button',
  className = '',
  children,
  ...rest
}) {
  return (
    <button type={type} className={buttonClass(variant, className)} {...rest}>
      {children}
    </button>
  )
}
