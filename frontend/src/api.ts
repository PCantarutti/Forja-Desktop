// Token desta execução do app, exigido pelo backend nas rotas /api. Ausente quando a UI abre
// numa aba comum do navegador (dev com Vite) — e ali o backend também não exige.
const auth = (): Record<string, string> => (window.forja?.token ? { "X-Forja-Token": window.forja.token } : {});

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...auth(), ...init?.headers },
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    // O status vai junto: alguns erros são perguntas (409 = precisa de confirmação), não falhas.
    throw Object.assign(new Error(body.detail ?? `HTTP ${r.status}`), { status: r.status });
  }
  // Resposta sem corpo (204, ou um DELETE que não devolve nada) fazia o r.json() estourar com um
  // "Unexpected end of JSON input" que não dizia nada sobre a chamada que falhou.
  const texto = await r.text();
  return (texto ? JSON.parse(texto) : null) as T;
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
  const r = await fetch(`/api/uploads?conv=${conv ?? 0}`, { method: "POST", body: form, headers: auth() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json();
}

/** Imagem de fora para editar na aba Imagens; volta o caminho absoluto no disco. */
export async function uploadReferencia(file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch("/api/imagens/referencia", { method: "POST", body: form, headers: auth() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return (await r.json()).path;
}

/** Lê um SSE via fetch (EventSource não faz POST nem aceita AbortSignal). Chama onEvent a cada `data:`. */
export async function streamSSE(path: string, init: RequestInit, onEvent: (ev: any) => void) {
  const r = await fetch(`/api${path}`, { ...init, headers: { "Content-Type": "application/json", ...auth(), ...init.headers } });
  if (!r.ok || !r.body) {
    const b = await r.json().catch(() => ({}));
    // O status vai junto, como no req(): 409 é pergunta (confirmar), não falha.
    throw Object.assign(new Error(b.detail ?? `HTTP ${r.status}`), { status: r.status });
  }
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  const SEPARADOR = String.fromCharCode(10, 10);
  let buf = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i;
      while ((i = buf.indexOf(SEPARADOR)) >= 0) {
        const chunk = buf.slice(0, i);
        buf = buf.slice(i + 2);
        for (const line of chunk.split(String.fromCharCode(10))) {
          if (!line.startsWith("data: ")) continue;
          // Um frame quebrado não pode derrubar a execução inteira: a UI cairia no finally,
          // recarregaria do banco e mostraria o turno como terminado enquanto ele segue vivo
          // no servidor — o pior dos dois mundos, porque some sem dizer que sumiu.
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {
            /* frame quebrado: segue lendo os próximos */
          }
        }
      }
    }
  } finally {
    reader.cancel().catch(() => {}); // solta o corpo da resposta mesmo se onEvent estourar
  }
}
