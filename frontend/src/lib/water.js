/*
 * Поле для шейдера воды (lib/waterLayer.js), построенное из эталонных масок.
 * Сами растры не меняются: из PNG маски модели и маски постоянной воды здесь
 * считаются расстояния до берега, и шейдер рисует по ним гладкий берег,
 * глубину и рябь. Каналы текстуры:
 *   R: расстояние со знаком до берега воды (плюс внутри), ±16 пикселей;
 *   G: то же для постоянной воды JRC;
 *   B: расстояние внутрь воды до 64 пикселей, это глубина и порядок разлива;
 *   A: 255 там, где есть вода.
 */
const RANGE = 16
const DEEP = 64

const cache = new Map()
const MAX_CACHED = 6

async function readAlpha(src) {
  const image = new Image()
  image.decoding = 'async'
  image.crossOrigin = 'anonymous'
  image.src = src
  await image.decode()
  const { naturalWidth: width, naturalHeight: height } = image
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d', { willReadFrequently: true })
  context.drawImage(image, 0, 0)
  const pixels = context.getImageData(0, 0, width, height).data
  const mask = new Uint8Array(width * height)
  for (let i = 0; i < mask.length; i++) mask[i] = pixels[i * 4 + 3] > 0 ? 1 : 0
  return { mask, width, height }
}

/** Расстояние от каждого пикселя, где mask === inside, до ближайшего пикселя снаружи. Двухпроходная фаска. */
function distance(mask, width, height, inside) {
  const d = new Float32Array(width * height)
  const far = width + height
  for (let i = 0; i < d.length; i++) d[i] = mask[i] === inside ? far : 0
  const diagonal = Math.SQRT2
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = y * width + x
      if (!d[i]) continue
      let best = d[i]
      if (x > 0) best = Math.min(best, d[i - 1] + 1)
      if (y > 0) {
        best = Math.min(best, d[i - width] + 1)
        if (x > 0) best = Math.min(best, d[i - width - 1] + diagonal)
        if (x < width - 1) best = Math.min(best, d[i - width + 1] + diagonal)
      }
      d[i] = best
    }
  }
  for (let y = height - 1; y >= 0; y--) {
    for (let x = width - 1; x >= 0; x--) {
      const i = y * width + x
      if (!d[i]) continue
      let best = d[i]
      if (x < width - 1) best = Math.min(best, d[i + 1] + 1)
      if (y < height - 1) {
        best = Math.min(best, d[i + width] + 1)
        if (x < width - 1) best = Math.min(best, d[i + width + 1] + diagonal)
        if (x > 0) best = Math.min(best, d[i + width - 1] + diagonal)
      }
      d[i] = best
    }
  }
  return d
}

function signed(mask, width, height) {
  const inside = distance(mask, width, height, 1)
  const outside = distance(mask, width, height, 0)
  return { inside, sdf: (i) => (mask[i] ? inside[i] - 0.5 : 0.5 - outside[i]) }
}

const encode = (value, range) => Math.round((Math.min(Math.max(value, -range), range) + range) / (2 * range) * 255)

export async function waterField(modelUrl, permanentUrl) {
  const key = modelUrl + '|' + (permanentUrl || '')
  if (cache.has(key)) return cache.get(key)
  const water = await readAlpha(modelUrl)
  let permanent = null
  if (permanentUrl) {
    try { permanent = await readAlpha(permanentUrl) } catch { permanent = null }
  }
  const { mask, width, height } = water
  const shore = signed(mask, width, height)
  const river = permanent && permanent.width === width ? signed(permanent.mask, width, height) : null
  const data = new Uint8Array(width * height * 4)
  for (let i = 0; i < mask.length; i++) {
    const o = i * 4
    data[o] = encode(shore.sdf(i), RANGE)
    data[o + 1] = river ? encode(river.sdf(i), RANGE) : 0
    data[o + 2] = Math.round(Math.min(shore.inside[i] - 0.5, DEEP) / DEEP * 255 * mask[i])
    data[o + 3] = mask[i] ? 255 : 0
  }
  const field = { data, width, height, hasPermanent: Boolean(river) }
  cache.set(key, field)
  if (cache.size > MAX_CACHED) cache.delete(cache.keys().next().value)
  return field
}
