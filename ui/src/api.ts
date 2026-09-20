async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${r.status}`);
  }
  return r.json();
}

export const api = {
  get: <T>(path: string) => req<T>(path),
  post: <T>(path: string, body?: unknown) =>
    req<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) => req<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => req<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T>(path: string) => req<T>(path, { method: "DELETE" }),
};

/** Upload de anexo (multipart). O arquivo vai parar dentro da pasta de trabalho. */
export async function uploadFile(file: File, conv: number | null = null) {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch(`/api/uploads?conv=${conv ?? 0}`, { method: "POST", body: form });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json();
}

/** Lê um SSE via fetch (EventSource não faz POST nem aceita AbortSignal). Chama onEvent a cada `data:`. */
export async function streamSSE(path: string, init: RequestInit, onEvent: (ev: any) => void) {
  const r = await fetch(`/api${path}`, { ...init, headers: { "Content-Type": "application/json", ...init.headers } });
  if (!r.ok || !r.body) {
    const b = await r.json().catch(() => ({}));
    throw new Error(b.detail ?? `HTTP ${r.status}`);
  }
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }
}
