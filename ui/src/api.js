import { useAuthStore } from './stores/auth.js'

async function request(method, path, body = null) {
  const auth = useAuthStore()
  const headers = {}
  if (auth.key) headers['X-API-Key'] = auth.key
  if (body !== null) headers['Content-Type'] = 'application/json'

  const res = await fetch(path, {
    method,
    headers,
    body: body !== null ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    // HTTP/2 has no statusText, so fall back to the status code to avoid
    // throwing an Error with an empty message (which renders as a blank page).
    const err = new Error(data.message || res.statusText || `Request failed (HTTP ${res.status})`)
    err.status = res.status
    err.data = data
    throw err
  }

  return res.json()
}

export const api = {
  get:    (path)        => request('GET',    path),
  post:   (path, body)  => request('POST',   path, body),
  patch:  (path, body)  => request('PATCH',  path, body),
  delete: (path)        => request('DELETE', path),
}
