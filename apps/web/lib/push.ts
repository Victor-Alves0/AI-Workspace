// Web Push (notificações do navegador): registra o service worker, pede permissão
// e inscreve/desinscreve no PushManager, sincronizando a inscrição com o servidor.
// Requer contexto seguro (HTTPS ou localhost) — navegadores bloqueiam SW em HTTP.

import { api } from "@/lib/api";

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window &&
    window.isSecureContext
  );
}

function urlB64ToUint8Array(b64: string): Uint8Array {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const base64 = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export async function pushEnabled(): Promise<boolean> {
  if (!pushSupported()) return false;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = reg && (await reg.pushManager.getSubscription());
    return !!sub;
  } catch {
    return false;
  }
}

/** O Brave desliga o serviço de push do Google por padrão — é a causa mais comum do
 * `AbortError`. `navigator.brave.isBrave()` é a API oficial do próprio Brave. */
async function isBrave(): Promise<boolean> {
  const nav = navigator as Navigator & { brave?: { isBrave?: () => Promise<boolean> } };
  try {
    return (await nav.brave?.isBrave?.()) === true;
  } catch {
    return false;
  }
}

/** Traduz a exceção do navegador em algo que o usuário possa AGIR.
 * O `subscribe()` falha na conversa entre o navegador e o serviço de push (FCM), não
 * no nosso servidor — a mensagem crua ("Registration failed - push service error") não
 * diz nada a ninguém. */
async function subscribeFailure(e: unknown): Promise<Error> {
  const name = e instanceof DOMException ? e.name : "";
  if (name === "NotAllowedError") {
    return new Error("Permissão de notificação negada — libere-a no cadeado da barra de endereço.");
  }
  if (await isBrave()) {
    return new Error(
      "O Brave bloqueia o serviço de push por padrão. Abra brave://settings/privacy, ligue "
      + "“Usar serviços do Google para mensagens push” e reinicie o navegador.",
    );
  }
  return new Error(
    "O navegador não conseguiu falar com o serviço de push. Uma extensão, firewall ou proxy "
    + "pode estar bloqueando — verifique e tente de novo.",
  );
}

/** Pede permissão, registra o SW e inscreve; sincroniza com o servidor. */
export async function enablePush(): Promise<void> {
  if (!pushSupported()) throw new Error("Notificações não são suportadas neste navegador (precisa de HTTPS).");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Permissão de notificação negada.");
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const { public_key } = await api.get<{ public_key: string }>("/push/vapid");
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    try {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(public_key) as BufferSource,
      });
    } catch (e) {
      console.error("pushManager.subscribe falhou:", e);  // a causa crua não pode se perder
      throw await subscribeFailure(e);
    }
  }
  const j = sub.toJSON() as { endpoint?: string; keys?: Record<string, string> };
  await api.post("/push/subscribe", { endpoint: j.endpoint, keys: j.keys ?? {}, ua: navigator.userAgent.slice(0, 200) });
}

export async function disablePush(): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = reg && (await reg.pushManager.getSubscription());
  if (sub) {
    await api.post("/push/unsubscribe", { endpoint: sub.endpoint, keys: {} }).catch(() => {});
    await sub.unsubscribe().catch(() => {});
  }
}
