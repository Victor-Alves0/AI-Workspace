// Tracer do cliente: mede o tempo de uma ação do usuário (do clique ao fim) e o
// envia como beacon, para o rastro cobrir "do clique ao 'oi'". O trecho de rede +
// servidor já vive no trace do backend; aqui somamos o pedaço que só o navegador
// enxerga. O beacon é best-effort: nunca atrasa nem quebra a ação.

import { API_URL, getLastTraceId } from "./api";

interface BeaconInit {
  action: string;
  path?: string;
  totalMs: number;
  ttfbMs?: number;
  renderMs?: number;
  ok?: boolean;
  traceId?: string | null;
}

function sendBeacon(b: BeaconInit): void {
  const body = JSON.stringify({
    trace_id: b.traceId ?? getLastTraceId(),
    action: b.action,
    path: b.path ?? (typeof location !== "undefined" ? location.pathname : ""),
    total_ms: Math.round(b.totalMs),
    ttfb_ms: Math.round(b.ttfbMs ?? 0),
    render_ms: Math.round(b.renderMs ?? 0),
    ok: b.ok ?? true,
  });
  try {
    // sendBeacon sobrevive à navegação/fechamento; cai no fetch quando ausente.
    // Ambos mandam cookies (a rota exige sessão).
    if (navigator.sendBeacon) {
      navigator.sendBeacon(
        `${API_URL}/observability/client`,
        new Blob([body], { type: "application/json" }),
      );
    } else {
      void fetch(`${API_URL}/observability/client`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" }, body, keepalive: true,
      });
    }
  } catch {
    /* telemetria nunca atrapalha */
  }
}

/** Mede uma ação assíncrona do usuário e emite o beacon ao terminar. Devolve o
 *  resultado da própria ação — é um wrapper transparente. */
export async function measure<T>(
  action: string, fn: () => Promise<T>,
): Promise<T> {
  const t0 = performance.now();
  let ok = true;
  try {
    return await fn();
  } catch (e) {
    ok = false;
    throw e;
  } finally {
    sendBeacon({ action, totalMs: performance.now() - t0, ok });
  }
}

/** Emite um beacon avulso (ex.: tempo até o primeiro token do chat). */
export function beacon(action: string, totalMs: number, extra?: Partial<BeaconInit>): void {
  sendBeacon({ action, totalMs, ...extra });
}
