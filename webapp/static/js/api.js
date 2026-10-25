// HTTP helpers: JSON requests, SSE job streams, and mock mode (?mock=1 serves static/mock fixtures instead of /api).
export const MOCK = new URLSearchParams(location.search).get("mock") === "1";

export class ApiError extends Error {
  constructor(status, error, message, body) {
    super(message || error || `HTTP ${status}`);
    this.status = status; this.error = error; this.body = body;
  }
}

let mockMod = null;
const mock = async () => (mockMod ||= await import("./mock.js"));

async function parse(res) {
  let body = null;
  const text = await res.text();
  try { body = text ? JSON.parse(text) : null; } catch { body = null; }
  if (!res.ok) throw new ApiError(res.status, body?.error || "http_error", body?.message || `HTTP ${res.status}`, body);
  return body;
}

async function request(path, init) {
  try { return await parse(await fetch(path, { cache: "no-store", ...init })); }
  catch (e) {
    if (e instanceof ApiError) throw e;
    if (e?.name === "AbortError") throw e;
    throw new ApiError(0, "network", "Server not reachable. Is `python -m webapp.server` running?");
  }
}

export async function fetchJSON(path, opts = {}) {
  if (MOCK && path.startsWith("/api/")) return (await mock()).get(path);
  return request(path, { signal: opts.signal });
}

export async function postJSON(path, body = {}, opts = {}) {
  if (MOCK && path.startsWith("/api/")) return (await mock()).post(path, body);
  return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: opts.signal });
}

export async function fetchText(path) {
  if (MOCK) return (await mock()).text(path);
  try {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) throw new ApiError(res.status, "http_error", `HTTP ${res.status}`);
    return await res.text();
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, "network", "Server not reachable.");
  }
}

/** Follow a job's events. Returns {close()}. onEnd gets the job_end event, or {status:"lost"} if the stream broke. */
export function openJobStream(job, onEvent, onEnd, after = 0) {
  if (MOCK && job.events_url.startsWith("mock:")) {
    let closed = false, stop = null;
    mock().then((m) => { if (!closed) stop = m.stream(job, onEvent, onEnd); });
    return { close() { closed = true; stop?.(); }, mock: true };
  }
  let last = after, es = null, done = false;
  const open = () => {
    es = new EventSource(`${job.events_url}${job.events_url.includes("?") ? "&" : "?"}after=${last}`);
    es.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch { return; }
      if (m.lastEventId) last = Number(m.lastEventId) || last;
      if (ev.type === "job_end") { done = true; es.close(); onEvent(ev); onEnd?.(ev); return; }
      onEvent(ev);
    };
    es.onerror = () => { if (done) return; es.close(); onEnd?.({ status: "lost", type: "lost" }); };
  };
  open();
  return { close() { done = true; es?.close(); }, reconnect() { done = false; es?.close(); open(); }, get last() { return last; } };
}
