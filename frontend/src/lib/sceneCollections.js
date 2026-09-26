export function sceneCollection(scene) {
  if (scene?.collection) return scene.collection
  if (['live_sentinel1_rtc', 'uploaded_sentinel1_geotiff'].includes(scene?.source_kind)) return 'showcase'
  return /^(Bolivia|Ghana|India|Mekong|Nigeria|Pakistan|Paraguay|Somalia|Spain|Sri-Lanka|USA)_\d+$/.test(scene?.chip_id || '') ? 'official' : 'showcase'
}
export const COLLECTIONS = [
  {value:'official',label:'По ТЗ',hint:'Официальный набор Sen1Floods11'},
  {value:'showcase',label:'Наши демо',hint:'Города и дополнительные снимки Sentinel‑1'},
]
