/** Base URL of the REST API, e.g. `http://localhost:8080/api/v1` in dev or
 * `/api/v1` in production once Caddy proxies both origins together. */
export const apiBaseUrl: string = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

/** Base URL of the WebSocket status channel, derived from {@link apiBaseUrl}
 * unless overridden -- http(s) and ws(s) always pair up on the same host. */
export const wsBaseUrl: string =
  import.meta.env.VITE_WS_BASE_URL ?? apiBaseUrl.replace(/^http/, 'ws')
