const API_KEY_STORAGE = 'ugen-gateway-api-key';

export const apiUrl = (import.meta.env.VITE_GATEWAY_URL || '/api').replace(/\/$/, '');

export function loadApiKey(): string {
  try { return localStorage.getItem(API_KEY_STORAGE) ?? ''; } catch { return ''; }
}

export function saveApiKey(key: string): void {
  try {
    if (key) localStorage.setItem(API_KEY_STORAGE, key);
    else localStorage.removeItem(API_KEY_STORAGE);
  } catch {
    // Storage can be unavailable (private mode); the key then lasts for this page only.
  }
}

/** Fetch a gateway path, sending the saved API key as a bearer token. */
export function apiFetch(path: string, init: RequestInit = {}, apiKey = loadApiKey()): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (apiKey) headers.set('Authorization', `Bearer ${apiKey}`);
  return fetch(`${apiUrl}${path}`, { ...init, headers });
}

/** The error message from a failed gateway response, in the gateway's own words. */
export async function errorMessage(response: Response): Promise<string> {
  const body: unknown = await response.json().catch(() => null);
  if (body && typeof body === 'object') {
    const data = body as { detail?: unknown; error?: { message?: unknown } };
    if (typeof data.detail === 'string') return data.detail;
    if (typeof data.error?.message === 'string') return data.error.message;
  }
  return `請求失敗（HTTP ${response.status}）`;
}
