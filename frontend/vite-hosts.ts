export function allowedHostsFromEnvironment(
  environment: Readonly<Record<string, string | undefined>>,
): string[] {
  const allowedHost = environment.VITE_ALLOWED_HOST?.trim()
  return allowedHost ? [allowedHost] : []
}
