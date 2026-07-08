"use client";

// Sistema de notificações: um "ding" curto (WebAudio, sem arquivo) + notificação
// nativa do navegador (quando a aba está em segundo plano e há permissão). O
// toast visual é renderizado pela UI (useToasts). Tudo degrada em silêncio.

let audioCtx: AudioContext | null = null;

/** Toca um "ding-dong" curto e discreto. Requer um gesto do usuário antes (o
 *  AudioContext só inicia após interação — o próprio envio de mensagem serve). */
export function playChime() {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    audioCtx ??= new Ctx();
    const ctx = audioCtx;
    if (ctx.state === "suspended") void ctx.resume();
    const now = ctx.currentTime;
    const notes: [number, number][] = [
      [880, 0],      // A5
      [1174.66, 0.12], // D6
    ];
    for (const [freq, t] of notes) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + t);
      gain.gain.exponentialRampToValueAtTime(0.16, now + t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + t + 0.2);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + t);
      osc.stop(now + t + 0.22);
    }
  } catch {
    /* sem áudio: ignora */
  }
}

/** Pede permissão de notificação nativa (uma vez), se ainda não decidida. */
export function requestNotifPermission() {
  try {
    if ("Notification" in window && Notification.permission === "default") {
      void Notification.requestPermission();
    }
  } catch {
    /* ignore */
  }
}

/** Notificação nativa do SO — só quando a aba NÃO está em foco (senão o toast
 *  in-app já basta) e há permissão concedida. */
export function browserNotify(title: string, body?: string) {
  try {
    if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
      const n = new Notification(title, { body, icon: "/logo.png" });
      n.onclick = () => { window.focus(); n.close(); };
    }
  } catch {
    /* ignore */
  }
}
