const pages = []
const overlays = []
let boundButton

function onBack() {
  const handler = overlays.at(-1)?.handler || pages.at(-1)?.handler
  handler?.()
}

function syncButton() {
  const button = window.Telegram?.WebApp?.BackButton
  if (boundButton && (boundButton !== button || (!pages.length && !overlays.length))) {
    boundButton.offClick(onBack)
    boundButton.hide()
    boundButton = undefined
  }
  if (!button) return
  if (!boundButton && (pages.length || overlays.length)) {
    button.onClick(onBack)
    boundButton = button
  }
  if (overlays.length || pages.at(-1)?.handler) button.show()
  else button.hide()
}

function register(stack, handler) {
  const entry = { handler }
  stack.push(entry)
  syncButton()
  return () => {
    const index = stack.indexOf(entry)
    if (index >= 0) stack.splice(index, 1)
    syncButton()
  }
}

export function bindBackButton(handler) {
  return register(pages, handler)
}

export function registerBackOverlay(handler) {
  return register(overlays, handler)
}
