import { test } from 'node:test'
import assert from 'node:assert/strict'
import { preloadGlobe, preloadTilt } from '../../src/lib/prefetch.js'

for (const style of [undefined, { version: 8 }]) {
  test(`background camera preload tolerates ${style ? 'an unloaded' : 'a removed'} map style`, async t => {
    const fetch = t.mock.method(globalThis, 'fetch', () => { throw new Error('No tiles should be requested') })
    const map = { getStyle: () => style }
    await assert.doesNotReject(() => preloadGlobe(map, [5.8, 9.1], 13))
    await assert.doesNotReject(() => preloadTilt(map, [5.8, 9.1], 13))
    assert.equal(fetch.mock.callCount(), 0)
  })
}
