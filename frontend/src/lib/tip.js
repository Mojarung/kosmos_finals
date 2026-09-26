/*
 * Одна плашка-подсказка на всё приложение, по мотивам krasnoyarsk_kosmos.
 * Пояснения, которые раньше стояли абзацами под каждым заголовком, живут здесь:
 * их видно по наведению, а панель остаётся про числа.
 */
const SHOW_DELAY_MS = 350
const HIDE_DELAY_MS = 140
const SKIP_DELAY_MS = 500
const GAP_PX = 10
const EDGE_PX = 8
// На касании подсказка открывается тапом по строке без собственного действия и сама гаснет.
const TOUCH_HOLD_MS = 3200
let touchTimer

const texts = new WeakMap()
let bubble = null
let showTimer
let hideTimer
let owner = null
let closedAt = 0
let guarded = false

function ensureBubble() {
  if (!bubble) {
    bubble = document.createElement('div')
    bubble.className = 'tip'
    bubble.setAttribute('role', 'tooltip')
    document.body.append(bubble)
  }
  return bubble
}

function place(target) {
  const tip = ensureBubble()
  const rect = target.getBoundingClientRect()
  // offsetWidth, а не getBoundingClientRect: закрытая плашка уменьшена scale.
  const width = tip.offsetWidth
  const height = tip.offsetHeight
  const above = rect.top - height - GAP_PX
  const fitsAbove = above >= EDGE_PX
  const maxLeft = Math.max(EDGE_PX, window.innerWidth - width - EDGE_PX)
  let left = Math.min(Math.max(rect.left + rect.width / 2 - width / 2, EDGE_PX), maxLeft)
  // У элемента в углу подсказка прижимается к его краю, чтобы связь читалась.
  if (left + width < rect.right) left = Math.min(Math.max(rect.right - width, EDGE_PX), maxLeft)
  else if (left > rect.left) left = Math.min(Math.max(rect.left, EDGE_PX), maxLeft)
  tip.style.left = Math.round(left) + 'px'
  tip.style.top = Math.round(fitsAbove ? above : rect.bottom + GAP_PX) + 'px'
  tip.dataset.side = fitsAbove ? 'top' : 'bottom'
}

function show(target) {
  const text = texts.get(target)
  if (!text || !target.isConnected) return hide()
  const tip = ensureBubble()
  const wasOpen = tip.dataset.open === 'true'
  owner = target
  tip.textContent = text
  place(target)
  // Закрытая плашка встаёт на место мгновенно и только потом открывается,
  // иначе переход по left/top протащил бы её через экран от прошлой точки.
  if (!wasOpen) void tip.offsetWidth
  tip.dataset.open = 'true'
}

function hide() {
  clearTimeout(showTimer)
  clearTimeout(hideTimer)
  hideTimer = undefined
  if (owner) closedAt = performance.now()
  owner = null
  if (bubble) bubble.dataset.open = 'false'
}

function interactive(target) {
  return target.matches('button, a, input, select, label, [role="tab"], [role="radio"]')
}
function onTouch(event) {
  if (event.pointerType !== 'touch') return
  const target = event.currentTarget
  // Кнопку тап нажимает, а не объясняет; у строк с числами действия нет, им подсказка нужнее.
  if (interactive(target)) return
  clearTimeout(touchTimer)
  if (owner === target) return hide()
  show(target)
  touchTimer = setTimeout(hide, TOUCH_HOLD_MS)
}

function onEnter(event) {
  // На касании наведения нет: подсказка всплыла бы после тапа поверх того, что открылось.
  if (event.pointerType === 'touch') return
  const target = event.currentTarget
  clearTimeout(hideTimer)
  hideTimer = undefined
  if (owner === target) return
  clearTimeout(showTimer)
  // Первая подсказка ждёт паузу, дальше по ряду элементов ходит сразу.
  if (owner || performance.now() - closedAt < SKIP_DELAY_MS) show(target)
  else showTimer = setTimeout(() => show(target), SHOW_DELAY_MS)
}
function onLeave() {
  clearTimeout(showTimer)
  if (owner && hideTimer === undefined) hideTimer = setTimeout(hide, HIDE_DELAY_MS)
}
function onFocus(event) {
  if (event.currentTarget.matches(':focus-visible')) show(event.currentTarget)
}

function onDown(event) {
  if (event.pointerType === 'touch' && !interactive(event.currentTarget)) return
  hide()
}

const EVENTS = [['pointerenter', onEnter], ['pointerleave', onLeave], ['focus', onFocus], ['blur', hide],
  ['pointerdown', onDown], ['pointerup', onTouch]]

function guard() {
  if (guarded) return
  guarded = true
  for (const name of ['wheel', 'keydown', 'blur']) window.addEventListener(name, hide, { capture: true, passive: true })
  window.addEventListener('pointerdown', event => {
    if (owner && !owner.contains(event.target)) hide()
  }, { capture: true, passive: true })
}

/** Подсказка для элемента вне шаблонов Vue (маркеры карты). Возвращает функцию отвязки. */
export function bindTip(el, text) {
  guard()
  texts.set(el, text)
  for (const [name, handler] of EVENTS) el.addEventListener(name, handler)
  return () => {
    for (const [name, handler] of EVENTS) el.removeEventListener(name, handler)
    texts.delete(el)
    if (owner === el) hide()
  }
}

export const vTip = {
  mounted(el, binding) {
    if (!guarded) {
      guarded = true
      for (const name of ['pointerdown', 'wheel', 'keydown', 'blur']) window.addEventListener(name, hide, { capture: true, passive: true })
    }
    if (binding.value) texts.set(el, binding.value)
    for (const [name, handler] of EVENTS) el.addEventListener(name, handler)
  },
  updated(el, binding) {
    if (binding.value) texts.set(el, binding.value)
    else texts.delete(el)
    if (owner === el) show(el)
  },
  unmounted(el) {
    for (const [name, handler] of EVENTS) el.removeEventListener(name, handler)
    if (owner === el) hide()
  },
}
