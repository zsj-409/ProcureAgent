// Minimal JSON API client with error propagation.

async function handle(response) {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (payload && payload.detail) {
        detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
      }
    } catch { /* keep default */ }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export async function getJSON(url) {
  return handle(await fetch(url));
}

export async function postJSON(url, body) {
  return handle(await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  }));
}
