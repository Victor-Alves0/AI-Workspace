import { API_URL } from "./api";

// Envia áudio gravado para STT e retorna o texto transcrito.
export async function transcribe(blob: Blob): Promise<string> {
  const fd = new FormData();
  fd.append("file", blob, "audio.webm");
  const res = await fetch(`${API_URL}/voice/stt`, {
    method: "POST",
    credentials: "include",
    body: fd,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "STT falhou");
  return (await res.json()).text ?? "";
}

// Voz do NAVEGADOR (speechSynthesis): grátis, offline, sempre presente. É o
// fallback de "Ler em voz alta" quando não há servidor de voz (sem chave e sem
// conexão local) — antes o botão falhava em silêncio.
function browserSpeak(text: string): Promise<void> {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) {
    throw new Error("Nenhum provedor de voz disponível");
  }
  window.speechSynthesis.cancel(); // clique novo cancela a fala anterior
  const u = new SpeechSynthesisUtterance(text);
  const pt = window.speechSynthesis.getVoices().find((v) => v.lang?.toLowerCase().startsWith("pt"));
  if (pt) u.voice = pt;
  u.lang = pt?.lang ?? "pt-BR";
  return new Promise<void>((resolve) => {
    u.onend = () => resolve();
    u.onerror = () => resolve();
    window.speechSynthesis.speak(u);
  });
}

// Áudio em reprodução no momento (para o barge-in do modo voz poder cortá-lo).
let _currentAudio: HTMLAudioElement | null = null;

// Interrompe qualquer fala em curso (servidor ou navegador). Usado quando o usuário
// volta a falar (barge-in) ou encerra o modo voz.
export function stopSpeaking(): void {
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    try { window.speechSynthesis.cancel(); } catch { /* noop */ }
  }
  if (_currentAudio) {
    try { _currentAudio.pause(); } catch { /* noop */ }
    _currentAudio = null;
  }
}

// Converte texto em fala (TTS) e toca o áudio. Servidor primeiro (voz local/
// OpenAI); qualquer falha cai na voz do navegador. Resolve quando a fala TERMINA
// (não só quando começa) — o modo voz espera isso antes de voltar a ouvir.
export async function speak(text: string, voice?: string): Promise<void> {
  try {
    const res = await fetch(`${API_URL}/voice/tts`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "TTS falhou");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    _currentAudio = audio;
    await new Promise<void>((resolve) => {
      audio.onended = () => { URL.revokeObjectURL(url); if (_currentAudio === audio) _currentAudio = null; resolve(); };
      audio.onerror = () => { URL.revokeObjectURL(url); if (_currentAudio === audio) _currentAudio = null; resolve(); };
      audio.onpause = () => { URL.revokeObjectURL(url); resolve(); };  // barge-in
      audio.play().catch(() => resolve());
    });
  } catch {
    await browserSpeak(text);
  }
}

// Ditado pelo NAVEGADOR (SpeechRecognition) — melhor esforço, roda em PARALELO
// com a gravação: se a transcrição do servidor falhar (sem chave/servidor), o
// texto reconhecido localmente salva o ditado. `null` se o navegador não tem a
// API (ou ela falha ao iniciar).
export function startBrowserDictation(): { stop: () => Promise<string> } | null {
  const w = window as unknown as Record<string, unknown>;
  const Ctor = (w.SpeechRecognition ?? w.webkitSpeechRecognition) as
    | (new () => {
        lang: string; continuous: boolean; interimResults: boolean;
        onresult: ((e: unknown) => void) | null; onend: (() => void) | null;
        onerror: (() => void) | null; start(): void; stop(): void;
      })
    | undefined;
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.lang = navigator.language || "pt-BR";
  rec.continuous = true;
  rec.interimResults = false;
  let text = "";
  rec.onresult = (e) => {
    const ev = e as { resultIndex: number; results: { length: number; [i: number]: { isFinal: boolean; 0: { transcript: string } } } };
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      if (ev.results[i].isFinal) text += (text ? " " : "") + ev.results[i][0].transcript.trim();
    }
  };
  let ended!: () => void;
  const done = new Promise<void>((r) => { ended = r; });
  rec.onend = () => ended();
  rec.onerror = () => { /* sem rede (Brave sem serviços Google) etc.: fica vazio */ };
  try {
    rec.start();
  } catch {
    return null;
  }
  return {
    stop: () => {
      try { rec.stop(); } catch { /* já parado */ }
      // dá até 1,5s p/ o resultado final chegar depois do stop
      return Promise.race([done, new Promise<void>((r) => setTimeout(r, 1500))]).then(() => text);
    },
  };
}

// Gravação simples via MediaRecorder. Retorna um controlador com stop().
export async function startRecording(): Promise<{ stop: () => Promise<Blob> }> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const rec = new MediaRecorder(stream);
  const chunks: BlobPart[] = [];
  rec.ondataavailable = (e) => chunks.push(e.data);
  rec.start();
  return {
    stop: () =>
      new Promise<Blob>((resolve) => {
        rec.onstop = () => {
          stream.getTracks().forEach((t) => t.stop());
          resolve(new Blob(chunks, { type: "audio/webm" }));
        };
        rec.stop();
      }),
  };
}
