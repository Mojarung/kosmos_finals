// Флаги стран каталога Sen1Floods11 с бесплатного flagcdn.com (SVG без ключа).
// Ключ — префикс chip_id; Меконг — снимки из Камбоджи.
export const COUNTRY_CODES = {
  Surgut: 'ru', Civita: 'it', Paiporta: 'es', Bolivia: 'bo', Ghana: 'gh', India: 'in', Mekong: 'kh', Nigeria: 'ng', Pakistan: 'pk',
  Paraguay: 'py', Somalia: 'so', Spain: 'es', 'Sri-Lanka': 'lk', USA: 'us',
}

export const eventKey = chipId => chipId?.split('_')[0]

export function flagUrl(key) {
  const code = COUNTRY_CODES[key]
  return code ? `https://flagcdn.com/${code}.svg` : null
}
