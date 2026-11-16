export async function api(path, { user, method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { "X-User-Id": user, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error;
    throw new Error(err ? `${err.code}: ${err.message}` : `HTTP ${res.status}`);
  }
  return data;
}
