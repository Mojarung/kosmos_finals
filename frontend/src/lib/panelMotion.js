import { onBeforeUnmount } from 'vue'

export const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

// Keep native <details> semantics, including Enter/Space and nested disclosures.
// A second click reverses from the visible height instead of queuing animations.
export function useDisclosureMotion() {
  const running = new Map()
  function finish(details, entry) {
    if (running.get(details) !== entry) return
    details.open = entry.open
    entry.animation.cancel()
    running.delete(details)
  }
  function stop() {
    for (const [details, entry] of running) finish(details, entry)
  }
  function click(event) {
    const summary = event.target.closest('summary')
    const details = summary?.parentElement
    if (!details?.matches('details.disclosure') || !event.currentTarget.contains(details)) return
    if (event.target.closest('a, button, input, select')) return
    event.preventDefault()
    const previous = running.get(details)
    const open = !(previous?.open ?? details.open)
    const from = details.getBoundingClientRect().height
    previous?.animation.cancel()
    running.delete(details)
    if (reducedMotion()) { details.open = open; return }
    details.open = open
    const to = details.getBoundingClientRect().height
    details.open = true
    const animation = details.animate([
      { height: `${from}px`, overflow: 'hidden' },
      { height: `${to}px`, overflow: 'hidden' },
    ], { duration: 260, easing: 'cubic-bezier(.22, 1, .36, 1)' })
    const entry = { animation, open }
    running.set(details, entry)
    animation.onfinish = () => finish(details, entry)
  }
  onBeforeUnmount(stop)
  return { click, stop }
}
