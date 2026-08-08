const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

export async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE_URL}${path}`, options);
  } catch {
    throw new Error('Cannot reach the Contextprobe API.');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed with status ${response.status}.`);
  }
  return response.status === 204 ? null : response.json();
}

export function send(method, body) {
  return {
    method,
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  };
}
