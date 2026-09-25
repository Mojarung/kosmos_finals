import { state } from '../store'

const KEY = 'floodvalue-theme'
let fadeTimer

function apply(theme) {
  state.theme = theme
  document.documentElement.dataset.theme = theme
  try { localStorage.setItem(KEY, theme) } catch {}
}

export function initTheme() {
  let saved = null
  try { saved = localStorage.getItem(KEY) } catch {}
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  apply(saved === 'dark' || saved === 'light' ? saved : prefersDark ? 'dark' : 'light')
}

/*
 * Новая тема раскрывается кругом из центра кнопки (View Transitions API).
 * Центр кнопки, а не курсор: так работает и с клавиатуры. Без поддержки API
 * цвета просто плавно перетекают.
 */
export function toggleTheme(event) {
  const next = state.theme === 'dark' ? 'light' : 'dark'
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const rect = event?.currentTarget?.getBoundingClientRect()
  if (!document.startViewTransition || reduced || !rect) {
    if (!reduced) {
      const root = document.documentElement
      root.classList.add('theme-anim')
      clearTimeout(fadeTimer)
      fadeTimer = setTimeout(() => root.classList.remove('theme-anim'), 780)
    }
    apply(next)
    return
  }
  const x = rect.left + rect.width / 2
  const y = rect.top + rect.height / 2
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y))
  // Новый кадр в круге живой: без этого в нём доигрывали бы CSS-переходы цветов
  // (заливка стекла, кнопки), и первые мгновения круг показывал бы старую тему.
  const root = document.documentElement
  root.classList.add('theme-switching')
  const transition = document.startViewTransition(() => {
    apply(next)
    // Пересчёт стилей, пока переходы выключены: цвета встают сразу, а не после снятия класса.
    void getComputedStyle(root).color
  })
  transition.finished.finally(() => root.classList.remove('theme-switching'))
  transition.ready.then(() => {
    document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 900, easing: 'cubic-bezier(0.2, 0, 0, 1)', pseudoElement: '::view-transition-new(root)' },
    )
  })
}
