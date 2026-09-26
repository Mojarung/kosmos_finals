import * as maplibregl from 'maplibre-gl'

/*
 * Живая вода: WebGL-слой MapLibre, который рисует маску модели как воду.
 * Берег гладкий по полю расстояний (lib/water.js), глубина от бирюзы у берега
 * к синему посередине, рябь и блики движутся, у кромки накатывает пена.
 * Паводок светлее и живее, постоянная вода JRC глубже и спокойнее, чтобы
 * их было видно без легенды. При открытии сцены вода разливается от русла
 * к берегам.
 *
 * Бережём машину: кадр не чаще 30 раз в секунду, только пока слой виден
 * и снимок в кадре; при prefers-reduced-motion и на слабом железе вода
 * замирает, но остаётся той же картинкой. Сами растры и их суммы не меняются.
 */
const EXTENT = 8192
const TILE_ZOOM = 12
const GRID = 24
const FRAME_MS = 1000 / 30
const REVEAL_MS = 1900
const SLOW_FRAME_MS = 55

const PALETTES = {
  light: { shallow: [0.55, 0.86, 0.92], deep: [0.13, 0.45, 0.72], foam: [0.95, 0.99, 1.0], river: [0.06, 0.2, 0.52] },
  dark: { shallow: [0.36, 0.72, 0.86], deep: [0.07, 0.27, 0.5], foam: [0.8, 0.93, 0.98], river: [0.04, 0.12, 0.34] },
}

const FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
uniform sampler2D u_field;
uniform vec2 u_size;
uniform float u_time;
uniform float u_opacity;
uniform float u_reveal;
uniform float u_scale;
uniform float u_permanent;
uniform vec3 u_shallow;
uniform vec3 u_deep;
uniform vec3 u_foam;
uniform vec3 u_river;
out vec4 fragColor;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
// Высота ряби и её наклон: четыре бегущие волны, искривлённые шумом, чтобы не было сетки.
vec3 waves(vec2 p, float t, float calm) {
  vec2 warp = vec2(noise(p * 0.21 + t * 0.03), noise(p * 0.19 - t * 0.025)) * 2.4;
  vec2 q = p + warp;
  vec2 d1 = vec2(0.82, 0.57), d2 = vec2(-0.35, 0.94), d3 = vec2(0.97, -0.24), d4 = vec2(-0.71, -0.7);
  float f1 = 0.9, f2 = 1.37, f3 = 2.3, f4 = 3.1;
  float s1 = 0.8 * t, s2 = 1.05 * t, s3 = 1.4 * t, s4 = 1.8 * t;
  float h = sin(dot(q, d1) * f1 + s1) * 0.5 + sin(dot(q, d2) * f2 + s2) * 0.3
          + sin(dot(q, d3) * f3 + s3) * 0.14 + sin(dot(q, d4) * f4 + s4) * 0.08;
  vec2 g = d1 * f1 * cos(dot(q, d1) * f1 + s1) * 0.5 + d2 * f2 * cos(dot(q, d2) * f2 + s2) * 0.3
         + d3 * f3 * cos(dot(q, d3) * f3 + s3) * 0.14 + d4 * f4 * cos(dot(q, d4) * f4 + s4) * 0.08;
  return vec3(h, g * calm);
}

void main() {
  vec4 field = texture(u_field, v_uv);
  float sd = field.r * 32.0 - 16.0;
  float inner = field.b * 64.0;
  float depthPx = sd < 15.0 ? sd : max(inner, sd);
  // Разлив: вода проступает от самых глубоких мест к берегу, фронт светлее.
  float front = mix(66.0, -2.0, u_reveal);
  float level = depthPx - front;
  float aa = max(fwidth(sd), 0.35);
  float edge = smoothstep(-aa, aa, sd) * smoothstep(-1.2, 0.6, level);
  if (edge <= 0.002) discard;

  // Слой JRC снят 30-метровыми блоками: его край размыт шире, иначе на реке видна лесенка.
  float permanent = u_permanent * smoothstep(-2.6, 2.6, field.g * 32.0 - 16.0);
  vec2 p = v_uv * u_size * u_scale;
  float t = u_time;
  // Постоянная вода спокойнее и течёт медленнее паводка.
  vec3 w = waves(p, t * mix(1.0, 0.55, permanent), mix(1.0, 0.6, permanent));
  vec3 n = normalize(vec3(-w.y * 0.34, -w.z * 0.34, 1.0));
  vec3 sun = normalize(vec3(-0.45, 0.55, 0.7));
  float diffuse = clamp(dot(n, sun), 0.0, 1.0);
  float glint = pow(max(dot(reflect(-sun, n), vec3(0.0, 0.0, 1.0)), 0.0), 48.0);
  float sparkle = glint * smoothstep(0.55, 0.75, noise(p * 1.7 + t * 0.5));

  float depth = smoothstep(0.0, 18.0, depthPx);
  vec3 color = mix(u_shallow, u_deep, depth);
  color = mix(color, u_river, permanent * 0.85);
  color *= 0.78 + 0.34 * diffuse;
  // Крупная зыбь: медленные светлые и тёмные полосы, видные даже с отдаления.
  color *= 0.94 + 0.12 * w.x;
  // Солнечные пятна на мелководье: паводок над сушей просвечивает.
  float caustic = pow(abs(sin(p.x * 1.3 + w.x * 2.0 + t * 0.6) * sin(p.y * 1.3 - w.x * 2.0 - t * 0.5)), 6.0);
  color += caustic * (1.0 - depth) * (1.0 - permanent) * 0.12;
  // Пена у берега набегает полосами и тает к воде.
  float shore = 1.0 - smoothstep(0.0, 3.2, sd);
  float lap = 0.5 + 0.5 * sin(sd * 2.3 - t * 1.7 + noise(p * 0.6) * 5.0);
  float foam = shore * (0.35 + 0.65 * lap) * (1.0 - permanent * 0.6);
  foam = max(foam, 1.0 - smoothstep(0.0, 0.9, sd));
  float wave = smoothstep(0.0, 1.6, abs(level)) ;
  float frontGlow = (1.0 - wave) * (1.0 - step(0.999, u_reveal));
  color = mix(color, u_foam, clamp(foam * 0.55 + sparkle * 0.9 + glint * 0.35 + frontGlow * 0.6, 0.0, 1.0));

  float alpha = edge * u_opacity * (0.8 + 0.18 * depth);
  fragColor = vec4(color * alpha, alpha);
}`

function compile(gl, type, source) {
  const shader = gl.createShader(type)
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const message = gl.getShaderInfoLog(shader)
    gl.deleteShader(shader)
    throw new Error('Water shader: ' + message)
  }
  return shader
}

const UNIFORMS = ['u_projection_fallback_matrix', 'u_projection_matrix', 'u_projection_tile_mercator_coords',
  'u_projection_clipping_plane', 'u_projection_transition', 'u_field', 'u_size', 'u_time', 'u_opacity',
  'u_reveal', 'u_scale', 'u_permanent', 'u_shallow', 'u_deep', 'u_foam', 'u_river']

export class WaterLayer {
  constructor(id, { onError } = {}) {
    this.id = id
    this.type = 'custom'
    this.renderingMode = '2d'
    this.programs = new Map()
    this.opacity = 0.7
    this.visible = true
    this.theme = 'light'
    this.reveal = 1
    this.revealStart = null
    this.field = null
    this.bbox = null
    this.onError = onError
    this.animated = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
    this.slowFrames = 0
    this.lastFrame = 0
    this.loop = this.loop.bind(this)
  }

  onAdd(map, gl) {
    this.map = map
    this.gl = gl
    this.texture = gl.createTexture()
    this.buffer = gl.createBuffer()
    this.indices = gl.createBuffer()
    this.vao = gl.createVertexArray()
    if (this.field) this.upload()
    if (this.bbox) this.mesh()
    this.frame = requestAnimationFrame(this.loop)
  }

  onRemove(_map, gl) {
    cancelAnimationFrame(this.frame)
    for (const { program } of this.programs.values()) gl.deleteProgram(program)
    this.programs.clear()
    gl.deleteTexture(this.texture)
    gl.deleteBuffer(this.buffer)
    gl.deleteBuffer(this.indices)
    gl.deleteVertexArray(this.vao)
    this.gl = null
  }

  program(gl, shaderData) {
    if (this.programs.has(shaderData.variantName)) return this.programs.get(shaderData.variantName)
    const vertex = `#version 300 es
${shaderData.vertexShaderPrelude}
${shaderData.define}
in vec2 a_pos;
in vec2 a_uv;
out vec2 v_uv;
void main() {
  gl_Position = projectTile(a_pos);
  v_uv = a_uv;
}`
    const program = gl.createProgram()
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vertex))
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT))
    // Постоянные номера атрибутов: один VAO подходит всем вариантам проекции.
    gl.bindAttribLocation(program, 0, 'a_pos')
    gl.bindAttribLocation(program, 1, 'a_uv')
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Water program: ' + gl.getProgramInfoLog(program))
    const entry = {
      program,
      uniforms: Object.fromEntries(UNIFORMS.map(name => [name, gl.getUniformLocation(program, name)])),
    }
    this.programs.set(shaderData.variantName, entry)
    return entry
  }

  /* Сетка в координатах плитки 12-го зума: так вершины точны до сантиметров даже на 17-м зуме. */
  mesh() {
    const gl = this.gl
    if (!gl || !this.bbox) return
    const [west, south, east, north] = this.bbox
    const origin = maplibregl.MercatorCoordinate.fromLngLat({ lng: west, lat: north })
    const scale = 2 ** TILE_ZOOM
    this.tile = { x: Math.floor(origin.x * scale), y: Math.floor(origin.y * scale), z: TILE_ZOOM }
    const vertices = []
    for (let row = 0; row <= GRID; row++) {
      for (let col = 0; col <= GRID; col++) {
        const u = col / GRID
        const v = row / GRID
        const point = maplibregl.MercatorCoordinate.fromLngLat({ lng: west + (east - west) * u, lat: north - (north - south) * v })
        vertices.push((point.x * scale - this.tile.x) * EXTENT, (point.y * scale - this.tile.y) * EXTENT, u, v)
      }
    }
    const indices = []
    for (let row = 0; row < GRID; row++) {
      for (let col = 0; col < GRID; col++) {
        const a = row * (GRID + 1) + col
        indices.push(a, a + 1, a + GRID + 1, a + 1, a + GRID + 2, a + GRID + 1)
      }
    }
    gl.bindVertexArray(this.vao)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.STATIC_DRAW)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 16, 0)
    gl.enableVertexAttribArray(1)
    gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 16, 8)
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.indices)
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(indices), gl.STATIC_DRAW)
    gl.bindVertexArray(null)
    gl.bindBuffer(gl.ARRAY_BUFFER, null)
    this.count = indices.length
  }

  upload() {
    const gl = this.gl
    if (!gl || !this.field) return
    gl.bindTexture(gl.TEXTURE_2D, this.texture)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, this.field.width, this.field.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, this.field.data)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
  }

  /** Новая сцена: поле, границы снимка и размер пикселя в метрах. reveal: разлить воду заново. */
  setScene(field, bbox, pixelMeters, { reveal = true } = {}) {
    this.field = field
    this.bbox = bbox
    // Рябь в метрах, а не в пикселях: на снимке с пикселем 10 м и 30 м волна одного размера.
    this.scale = (pixelMeters || 10) / 26
    this.upload()
    this.mesh()
    if (reveal && this.animated) { this.reveal = 0; this.revealStart = null } else this.reveal = 1
    this.map?.triggerRepaint()
  }

  clear() {
    this.field = null
    this.map?.triggerRepaint()
  }

  set(changes) {
    Object.assign(this, changes)
    this.map?.triggerRepaint()
  }

  playReveal() {
    if (!this.animated) return
    this.reveal = 0
    this.revealStart = null
    this.map?.triggerRepaint()
  }

  inView() {
    if (!this.map || !this.bbox) return false
    const bounds = this.map.getBounds()
    const [west, south, east, north] = this.bbox
    return this.map.getZoom() >= 7 && west < bounds.getEast() && east > bounds.getWest() && south < bounds.getNorth() && north > bounds.getSouth()
  }

  loop(now) {
    if (!this.gl) return
    this.frame = requestAnimationFrame(this.loop)
    const active = this.field && this.visible && this.opacity > 0 && !document.hidden
    const revealing = this.reveal < 1
    if (!active || (!this.animated && !revealing)) return
    const delta = now - this.lastFrame
    if (delta < FRAME_MS) return
    // Если кадры стабильно тянутся дольше 55 мс, машина не справляется: вода замирает.
    if (this.lastFrame && delta > SLOW_FRAME_MS && delta < 400) this.slowFrames++
    else if (delta <= SLOW_FRAME_MS) this.slowFrames = Math.max(0, this.slowFrames - 1)
    if (this.slowFrames > 90) this.animated = false
    this.lastFrame = now
    if (revealing) {
      this.revealStart ??= now
      const t = Math.min(1, (now - this.revealStart) / REVEAL_MS)
      this.reveal = 1 - (1 - t) ** 3
    }
    if (revealing || this.inView()) this.map.triggerRepaint()
  }

  render(gl, args) {
    if (!this.field || !this.visible || this.opacity <= 0 || !this.count) return
    let entry
    try {
      entry = this.program(gl, args.shaderData)
    } catch (error) {
      this.visible = false
      this.onError?.(error)
      return
    }
    const { program, uniforms: u } = entry
    const data = args.getProjectionData({ tileID: { wrap: 0, canonical: this.tile }, applyGlobeMatrix: true })
    gl.useProgram(program)
    gl.uniformMatrix4fv(u.u_projection_fallback_matrix, false, data.fallbackMatrix)
    gl.uniformMatrix4fv(u.u_projection_matrix, false, data.mainMatrix)
    gl.uniform4f(u.u_projection_tile_mercator_coords, ...data.tileMercatorCoords)
    gl.uniform4f(u.u_projection_clipping_plane, ...data.clippingPlane)
    gl.uniform1f(u.u_projection_transition, data.projectionTransition)
    const palette = PALETTES[this.theme] || PALETTES.light
    gl.uniform3f(u.u_shallow, ...palette.shallow)
    gl.uniform3f(u.u_deep, ...palette.deep)
    gl.uniform3f(u.u_foam, ...palette.foam)
    gl.uniform3f(u.u_river, ...palette.river)
    gl.uniform2f(u.u_size, this.field.width, this.field.height)
    gl.uniform1f(u.u_time, this.animated ? performance.now() / 1000 : 12.5)
    gl.uniform1f(u.u_opacity, this.opacity)
    gl.uniform1f(u.u_reveal, this.reveal)
    gl.uniform1f(u.u_scale, this.scale || 0.4)
    gl.uniform1f(u.u_permanent, this.field.hasPermanent ? 1 : 0)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, this.texture)
    gl.uniform1i(u.u_field, 0)
    gl.bindVertexArray(this.vao)
    gl.disable(gl.DEPTH_TEST)
    gl.disable(gl.STENCIL_TEST)
    gl.disable(gl.CULL_FACE)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    gl.drawElements(gl.TRIANGLES, this.count, gl.UNSIGNED_SHORT, 0)
    gl.bindVertexArray(null)
  }
}
