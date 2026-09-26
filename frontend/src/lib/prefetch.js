/*
 * Предзагрузка тайлов подложки в HTTP-кэш браузера. MapLibre потом запросит
 * те же адреса и получит их из кэша мгновенно. Кэш живёт на диске, а не в
 * памяти вкладки: в память тайл попадает, только когда его рисуют. Цена —
 * только трафик, поэтому окна маленькие, а при экономии трафика не качаем.
 */
const fetched = new Set()
const BATCH = 6

function lonLatToTile(lon, lat, z) {
  const n = 2 ** z
  const x = Math.floor(((lon + 180) / 360) * n)
  const rad = (lat * Math.PI) / 180
  const y = Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n)
  return [Math.min(Math.max(x, 0), n - 1), Math.min(Math.max(y, 0), n - 1)]
}

// Источники с шаблоном тайлов, которые сейчас видны на карте.
function tileSources(map) {
  const style = map.getStyle()
  const visible = new Set(style.layers
    .filter(layer => layer.source && map.getLayoutProperty(layer.id, 'visibility') !== 'none')
    .map(layer => layer.source))
  return [...visible]
    .map(id => map.getSource(id))
    .filter(source => ['vector', 'raster'].includes(source?.type) && source.tiles?.length)
    .map(source => ({
      template: source.tiles[0], minzoom: source.minzoom ?? 0, maxzoom: source.maxzoom ?? 22,
      tileSize: source.tileSize ?? 512,
      // Тайл 256 px рисуется на зум глубже, чем 512 px: при зуме карты 1,8 спутник берёт z2, вектор — z1.
      offset: (source.tileSize ?? 512) <= 256 ? 1 : 0,
    }))
}

/*
 * tilesAt(z, offset) отдаёт список [z, x, y] уже в зумах источника. Векторные
 * тайлы на мелких зумах тяжёлые (z2–z3 у OpenFreeMap — 1,2–1,5 МБ штука),
 * спутниковые лёгкие везде (12–19 КБ), поэтому считаем для каждого отдельно.
 */
function urls(map, tilesFor) {
  const out = []
  for (const source of tileSources(map)) {
    for (const [z, x, y] of tilesFor(source.offset, source.tileSize)) {
      if (z < source.minzoom || z > source.maxzoom) continue
      const url = source.template.replace('{z}', z).replace('{x}', x).replace('{y}', y)
      if (!fetched.has(url)) out.push(url)
    }
  }
  return out
}

function idle() {
  return new Promise(resolve => (window.requestIdleCallback || (cb => setTimeout(cb, 200)))(resolve, { timeout: 2000 }))
}

async function download(list) {
  if (navigator.connection?.saveData) return
  for (let i = 0; i < list.length; i += BATCH) {
    await idle()
    await Promise.all(list.slice(i, i + BATCH).map(url => {
      fetched.add(url)
      // Тело читаем до конца: недочитанный крупный ответ браузер может не сохранить в кэш.
      return fetch(url, { priority: 'low' }).then(response => {
        if (!response.ok) throw new Error(`Tile ${response.status}`)
        return response.arrayBuffer()
      }).catch(() => fetched.delete(url))
    }))
  }
}

function window2d(z, x, y, cols, rows, shiftY = 0) {
  const n = 2 ** z
  const tiles = []
  for (let dy = -rows; dy <= rows; dy++) {
    for (let dx = -cols; dx <= cols; dx++) {
      const ty = y + dy + shiftY
      if (ty < 0 || ty >= n) continue
      tiles.push([z, (x + dx + n) % n, ty])
    }
  }
  return tiles
}

/*
 * Шрифты подписей. На глобусе появляются названия стран и морей, и без
 * загруженных глифов подписи проступают с задержкой. Берём все шрифты стиля
 * и диапазоны латиницы (0–255, 256–511) и кириллицы (1024–1279).
 */
function glyphUrls(map) {
  const style = map.getStyle()
  if (!style.glyphs) return []
  const stacks = new Set()
  for (const layer of style.layers) {
    const font = layer.layout?.['text-font']
    if (Array.isArray(font) && font.every(item => typeof item === 'string')) stacks.add(font.join(','))
  }
  const out = []
  for (const stack of stacks) {
    for (const range of ['0-255', '256-511', '1024-1279']) {
      const url = style.glyphs.replace('{fontstack}', encodeURIComponent(stack)).replace('{range}', range)
      if (!fetched.has(url)) out.push(url)
    }
  }
  return out
}

/**
 * Глобус (зум карты 1,8): весь мир на уровне, который реально рисуется, —
 * вектор z0–1 (5 тайлов, ~1 МБ), спутник z0–2 (21 тайл, ~0,3 МБ). Плюс
 * «коридор» спуска: по 9 тайлов вокруг сцены на каждом втором зуме начиная
 * с 5 — там тайлы уже лёгкие, а тяжёлые z2–z4 камера пролетает за доли секунды.
 */
export function preloadGlobe(map, [lon, lat], zoom) {
  return download([...glyphUrls(map), ...urls(map, offset => {
    const tiles = []
    for (let z = 0; z <= 1 + offset; z++) for (let x = 0; x < 2 ** z; x++) for (let y = 0; y < 2 ** z; y++) tiles.push([z, x, y])
    for (let mapZoom = 5; mapZoom < Math.floor(zoom); mapZoom += 2) {
      const z = mapZoom + offset
      const [x, y] = lonLatToTile(lon, lat, z)
      tiles.push(...window2d(z, x, y, 1, 1))
    }
    return tiles
  })])
}

/**
 * 3D-наклон: рельефа нет, это только pitch камеры. При наклоне камера смотрит
 * к горизонту, и там нужны тайлы на 1–3 зума мельче в полосе к северу от
 * центра (камера развёрнута на −18°, то есть тоже к северу). На этих зумах
 * тайлы по 4–60 КБ.
 */
export function preloadTilt(map, [lon, lat], zoom) {
  return download(urls(map, offset => {
    const tiles = []
    for (let step = 1; step <= 3; step++) {
      const z = Math.floor(zoom) - step + offset
      if (z < 0) break
      const [x, y] = lonLatToTile(lon, lat, z)
      tiles.push(...window2d(z, x, y, 2, 1, -2))
    }
    return tiles
  }))
}

// Готовая сцена известна до клика: греем её PNG и тайлы конечного кадра,
// включая поля под боковыми панелями. Это лишь HTTP-кэш; готовность кадра
// после смены камеры всё равно проверяет сама карта.
export function preloadScene(map, scene, zoom) {
  const [w, s, e, n] = scene.bbox
  const center = [(w + e) / 2, (s + n) / 2]
  const images = ['model', 'permanent', 'radar', 'probability', 'baseline', 'reference', 'doubt']
    .map(name => `/api/demo/${scene.chip_id}/${name}.png`)
  return download([...images, ...urls(map, (offset, size) => {
    const z = Math.floor(zoom) + offset
    const [x, y] = lonLatToTile(...center, z)
    const cols = Math.min(6, Math.ceil(map.getCanvas().clientWidth / size / 2) + 1)
    const rows = Math.min(5, Math.ceil(map.getCanvas().clientHeight / size / 2) + 2)
    return window2d(z, x, y, cols, rows)
  })])
}

export function preloadJourney(map, from, to, zoom) {
  if (zoom < 3) return preloadGlobe(map, to, zoom)
  return download(urls(map, offset => {
    const z = Math.floor(zoom) + offset
    const tiles = []
    for (let i = 0; i <= 4; i++) {
      const t = i / 4
      const [x, y] = lonLatToTile(from[0] + (to[0] - from[0]) * t,
        from[1] + (to[1] - from[1]) * t, z)
      tiles.push(...window2d(z, x, y, 1, 1))
    }
    return tiles
  }))
}
