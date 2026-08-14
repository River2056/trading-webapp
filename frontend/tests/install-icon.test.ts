import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { expect, test } from 'vitest'

const frontendRoot = resolve(process.cwd())

const readText = (path: string) => readFileSync(resolve(frontendRoot, path), 'utf8')

const pngDimensions = (path: string) => {
  const data = readFileSync(resolve(frontendRoot, path))
  expect(data.subarray(1, 4).toString()).toBe('PNG')
  return { width: data.readUInt32BE(16), height: data.readUInt32BE(20) }
}

test('mobile installations use the growing-business home-screen icon', () => {
  const html = readText('index.html')
  expect(html).toContain('<link rel="manifest" href="/manifest.webmanifest">')
  expect(html).toContain('<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">')

  const manifest = JSON.parse(readText('public/manifest.webmanifest'))
  expect(manifest).toMatchObject({
    name: 'Paper Trading Only',
    short_name: 'Paper Trading',
    display: 'standalone',
    icons: expect.arrayContaining([
      expect.objectContaining({ src: '/icons/icon-192.png', sizes: '192x192' }),
      expect.objectContaining({ src: '/icons/icon-512.png', sizes: '512x512' }),
    ]),
  })

  expect(pngDimensions('public/icons/apple-touch-icon.png')).toEqual({ width: 180, height: 180 })
  expect(pngDimensions('public/icons/icon-192.png')).toEqual({ width: 192, height: 192 })
  expect(pngDimensions('public/icons/icon-512.png')).toEqual({ width: 512, height: 512 })
})
