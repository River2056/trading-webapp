import { describe, expect, it } from 'vitest'
import { allowedHostsFromEnvironment } from '../vite-hosts'

describe('Vite development server configuration', () => {
  it('allows the Tailscale hostname supplied by the launcher environment', () => {
    expect(
      allowedHostsFromEnvironment({
        VITE_ALLOWED_HOST: 'dev-node.example-tailnet.ts.net',
      }),
    ).toEqual(['dev-node.example-tailnet.ts.net'])
  })
})
