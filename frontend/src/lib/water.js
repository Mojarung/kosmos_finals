/*
 * Маска воды приходит плоской PNG одного цвета (эталонные растры не меняем).
 * Здесь из неё один раз на снимок и тему рисуется «вода»: гладкая береговая
 * линия по полю расстояний вместо ступенек 10-метровых пикселей, глубина от
 * бирюзы у берега к синему посередине, светлая кромка и мягкая рябь с бликами.
 * Картинка статичная: анимация заставила бы пересчитывать размытие стекла
 * под панелями на каждом кадре.
 */
const SCALE = 2
const DEPTH_PX = 10
const SHORE_PX = 1.6
const PALETTES = {
  light: { shallow: [150, 214, 234], deep: [36, 104, 168], foam: [240, 250, 253] },
  dark: { shallow: [92, 176, 212], deep: [20, 64, 112], foam: [200, 234, 246] },
}
// Прозрачный пиксель, пока вода считается: лучше пусто, чем старый плоский цвет.
export const BLANK = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII='

const cache = new Map()
const MAX_CACHED = 8

export function cachedWater(src, theme) {
  return cache.get(src + '|' + theme) || null
}

export async function waterImage(src, theme) {
  const key = src + '|' + theme
  if (cache.has(key)) return cache.get(key)
  const mask = await readMask(src)
  const url = URL.createObjectURL(await paint(mask, PALETTES[theme] || PALETTES.light))
  cache.set(key, url)
  if (cache.size > MAX_CACHED) {
    const [oldest] = cache.keys()
    URL.revokeObjectURL(cache.get(oldest))
    cache.delete(oldest)
  }
  return url
}

async function readMask(src) {
  const image = new Image()
  image.decoding = 'async'
  image.src = src
  await image.decode()
  const { naturalWidth: width, naturalHeight: height } = image
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d', { willReadFrequently: true })
  context.drawImage(image, 0, 0)
  const pixels = context.getImageData(0, 0, width, height).data
  const water = new Uint8Array(width * height)
  for (let i = 0; i < water.length; i++) water[i] = pixels[i * 4 + 3] > 0 ? 1 : 0
  return { water, width, height }
}

/** Расстояние от каждого пикселя, где inside(i), до ближайшего пикселя снаружи. Двухпроходная фаска. */
function distance(water, width, height, inside) {
  const d = new Float32Array(width * height)
  const far = width + height
  for (let i = 0; i < d.length; i++) d[i] = water[i] === inside ? far : 0
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

const smoothstep = (a, b, x) => {
  const t = Math.min(Math.max((x - a) / (b - a), 0), 1)
  return t * t * (3 - 2 * t)
}
const mix = (a, b, t) => a + (b - a) * t

function paint({ water, width, height }, palette) {
  // Поле со знаком: плюс внутри воды, минус на суше, в пикселях исходника.
  const inside = distance(water, width, height, 1)
  const outside = distance(water, width, height, 0)
  const sdf = new Float32Array(width * height)
  for (let i = 0; i < sdf.length; i++) sdf[i] = water[i] ? inside[i] - 0.5 : 0.5 - outside[i]

  const outWidth = width * SCALE
  const outHeight = height * SCALE
  const canvas = document.createElement('canvas')
  canvas.width = outWidth
  canvas.height = outHeight
  const context = canvas.getContext('2d')
  const image = context.createImageData(outWidth, outHeight)
  const out = image.data
  const { shallow, deep, foam } = palette

  for (let Y = 0; Y < outHeight; Y++) {
    const v = Math.min(Math.max((Y + 0.5) / SCALE - 0.5, 0), height - 1)
    const y0 = Math.floor(v)
    const y1 = Math.min(y0 + 1, height - 1)
    const fy = v - y0
    for (let X = 0; X < outWidth; X++) {
      const u = Math.min(Math.max((X + 0.5) / SCALE - 0.5, 0), width - 1)
      const x0 = Math.floor(u)
      const x1 = Math.min(x0 + 1, width - 1)
      const fx = u - x0
      // Билинейное поле даёт гладкий изгиб берега вместо лесенки пикселей.
      const s = mix(mix(sdf[y0 * width + x0], sdf[y0 * width + x1], fx), mix(sdf[y1 * width + x0], sdf[y1 * width + x1], fx), fy)
      const edge = smoothstep(-0.6, 0.6, s)
      const o = (Y * outWidth + X) * 4
      if (edge <= 0) { out[o + 3] = 0; continue }

      const depth = smoothstep(0, DEPTH_PX, s)
      // Крупная зыбь и мелкая рябь из синусов с искривлением: без повторяющейся сетки.
      const swell = (Math.sin(u * 0.21 + 1.7 * Math.sin(v * 0.07)) + Math.sin(v * 0.17 + 1.3 * Math.sin(u * 0.05))
        + 0.5 * Math.sin((u + v) * 0.33)) / 2.5
      const ripple = Math.sin(u * 0.9 + 2 * Math.sin(v * 0.4)) * Math.sin(v * 0.8 + 2 * Math.sin(u * 0.3))
      const wave = 0.65 * swell + 0.35 * ripple
      // Ветровые пятна: крупные участки светлее и темнее, как рябь от порывов.
      const gust = Math.sin(u * 0.031 + 2.2 * Math.sin(v * 0.023)) * Math.sin(v * 0.027 + 1.8 * Math.sin(u * 0.019))
      const shade = 1 + 0.13 * wave * (0.5 + 0.5 * depth) + 0.09 * gust
      // Гребни: рваные светлые штрихи двух направлений, проступают пятнами, а не сеткой.
      const patch = smoothstep(0.15, 0.85, 0.5 + 0.5 * Math.sin(u * 0.05 + 2.5 * Math.sin(v * 0.035)) * Math.sin(v * 0.06 + 2 * Math.sin(u * 0.04)))
      const crestA = Math.max(0, Math.sin(u * 0.13 - v * 0.37 + 3 * Math.sin(u * 0.021) + 2.4 * swell)) ** 18
      const crestB = Math.max(0, Math.sin(u * 0.29 + v * 0.11 + 2 * Math.sin(v * 0.027) + 1.6 * ripple)) ** 24
      const crest = (crestA * patch * 0.4 + crestB * (1 - patch) * 0.25) * (0.6 + 0.4 * gust)
      const glint = (Math.max(0, wave - 0.55) / 0.45) ** 2 * 0.6
      const light = Math.min(1, (crest + glint) * (0.35 + 0.65 * depth))
      const shore = s < SHORE_PX ? (1 - Math.max(s, 0) / SHORE_PX) * 0.7 : 0
      for (let c = 0; c < 3; c++) {
        let value = mix(shallow[c], deep[c], depth) * shade
        value = mix(value, foam[c], light)
        value = mix(value, foam[c], shore)
        out[o + c] = value
      }
      out[o + 3] = 255 * edge * (0.9 + 0.08 * depth)
    }
  }
  context.putImageData(image, 0, 0)
  return new Promise((resolve, reject) => canvas.toBlob(blob => (blob ? resolve(blob) : reject(new Error('water blob'))), 'image/png'))
}
