import { computed, ref } from 'vue'
import { state } from '../store'
import { COUNTRY_CODES, eventKey } from './flags'

// Справочная сумма в валюте страны снимка под рублёвой. Расчёт кейса остаётся в ₽ (ПП 840,
// учебные V и q), поэтому курс только подсказывает порядок цифр и ни во что не складывается.
const CURRENCIES = {
  bo: 'BOB', gh: 'GHS', in: 'INR', kh: 'KHR', ng: 'NGN', pk: 'PKR',
  py: 'PYG', so: 'SOS', es: 'EUR', lk: 'LKR', us: 'USD',
}
const RATES_URL = 'https://open.er-api.com/v6/latest/RUB'
const CACHE_KEY = 'fx-rub-v1'
const TTL_MS = 12 * 3600 * 1000

const rates = ref(null)

function readCache() {
  try {
    const cached = JSON.parse(localStorage.getItem(CACHE_KEY))
    return cached && Date.now() - cached.fetchedAt < TTL_MS ? cached : null
  } catch { return null }
}
async function loadRates() {
  const cached = readCache()
  if (cached) { rates.value = cached; return }
  try {
    const response = await fetch(RATES_URL)
    const data = await response.json()
    if (data.result !== 'success') return
    rates.value = { rates: data.rates, fetchedAt: Date.now() }
    try { localStorage.setItem(CACHE_KEY, JSON.stringify(rates.value)) } catch { /* без кеша */ }
  } catch { /* без сети сумма остаётся только в рублях */ }
}
loadRates()

// Страна известна только у снимков каталога Sen1Floods11; для живых и загруженных — нет.
export const localCurrency = computed(() => {
  const code = CURRENCIES[COUNTRY_CODES[eventKey(state.sceneId)]]
  const rate = code && rates.value?.rates[code]
  return rate ? { code, rate } : null
})

const formats = new Map()
function format(code, compact) {
  const key = code + compact
  if (!formats.has(key)) formats.set(key, new Intl.NumberFormat('ru-RU', {
    style: 'currency', currency: code, currencyDisplay: 'narrowSymbol',
    maximumFractionDigits: compact ? 1 : 0, ...(compact ? { notation: 'compact' } : {}),
  }))
  return formats.get(key)
}

/** 6 143,84 ₽ → «~ 98 585 ₦» по курсу дня; null, если страны или курса нет. */
export function localMoney(rubValue, compact = false) {
  const fx = localCurrency.value
  if (!fx || !rubValue) return null
  return '~ ' + format(fx.code, compact).format(rubValue * fx.rate)
}

export function localMoneyTip() {
  const fx = localCurrency.value
  if (!fx) return ''
  const symbol = format(fx.code, false).formatToParts(0).find(part => part.type === 'currency').value
  const n = value => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(value)
  // Курс в сторону, где число больше единицы: «1 ₽ = 16,05 ₦», но «1 € = 96,2 ₽».
  const quote = fx.rate >= 1 ? `1 ₽ = ${n(fx.rate)} ${symbol}` : `1 ${symbol} = ${n(1 / fx.rate)} ₽`
  return `${quote}, по данным ExchangeRate-API`
}
