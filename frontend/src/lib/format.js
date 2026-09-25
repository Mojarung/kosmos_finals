const intl = new Map()

export function number(value, digits = 0) {
  if (!intl.has(digits)) intl.set(digits, new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }))
  return intl.get(digits).format(value || 0)
}
export function rub(value) { return value == null ? '—' : number(value) + ' ₽' }
export function compactRub(value) {
  if (value == null) return '—'
  if (Math.abs(value) >= 1_000_000) return number(value / 1_000_000, 2) + ' млн ₽'
  if (Math.abs(value) >= 1_000) return number(value / 1_000, 1) + ' тыс. ₽'
  return rub(value)
}
export function km(value) { return value == null ? '—' : number(value, 2) + ' км²' }
export function percent(value, digits = 0) { return value == null ? '—' : number(value * 100, digits) + '%' }
const pluralRules = new Intl.PluralRules('ru-RU')
/** plural(2, ['зона', 'зоны', 'зон']) → «2 зоны» */
export function plural(count, [one, few, many]) {
  const form = pluralRules.select(count)
  return count + ' ' + (form === 'one' ? one : form === 'few' ? few : many)
}
export function date(value) {
  if (!value) return '—'
  return new Date(value).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).replace(/\s*г\.$/, '')
}
