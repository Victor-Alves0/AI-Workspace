import { API_URL } from "./api";

export interface SpeechProgress {
  phase: "idle" | "loading" | "playing" | "paused";
  currentTime: number;
  duration: number;
  rate: number;
  seekable: boolean;
}

const IDLE_SPEECH: SpeechProgress = {
  phase: "idle",
  currentTime: 0,
  duration: 0,
  rate: 1,
  seekable: false,
};

let _speechProgress: SpeechProgress = IDLE_SPEECH;
const _speechListeners = new Set<(state: SpeechProgress) => void>();

function publishSpeech(patch: Partial<SpeechProgress>): void {
  _speechProgress = { ..._speechProgress, ...patch };
  for (const listener of _speechListeners) listener(_speechProgress);
}

export function subscribeSpeechProgress(
  listener: (state: SpeechProgress) => void,
): () => void {
  _speechListeners.add(listener);
  listener(_speechProgress);
  return () => _speechListeners.delete(listener);
}

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
  u.rate = _speechProgress.rate;
  const estimatedDuration = Math.max(1, text.length / (14 * u.rate));
  return new Promise<void>((resolve) => {
    const done = () => {
      if (_currentBrowserResolve === done) {
        _currentBrowserResolve = null;
        _currentBrowserUtterance = null;
        publishSpeech({ ...IDLE_SPEECH });
      }
      resolve();
    };
    _currentBrowserResolve = done;
    _currentBrowserUtterance = u;
    u.onstart = () => publishSpeech({
      phase: "playing", currentTime: 0, duration: estimatedDuration, seekable: false,
    });
    u.onpause = () => publishSpeech({ phase: "paused" });
    u.onresume = () => publishSpeech({ phase: "playing" });
    u.onboundary = (event) => publishSpeech({
      currentTime: Math.max(0, event.elapsedTime),
      duration: estimatedDuration,
    });
    u.onend = done;
    u.onerror = done;
    window.speechSynthesis.speak(u);
  });
}

// Áudio em reprodução no momento (para o barge-in do modo voz poder cortá-lo).
let _currentAudio: HTMLAudioElement | null = null;
let _currentAudioFinish: (() => void) | null = null;
let _currentBrowserUtterance: SpeechSynthesisUtterance | null = null;
let _currentBrowserResolve: (() => void) | null = null;
let _speechGeneration = 0;
let _progressTimer: ReturnType<typeof setInterval> | null = null;

function clearProgressTimer(): void {
  if (_progressTimer) clearInterval(_progressTimer);
  _progressTimer = null;
}

function publishAudioProgress(audio: HTMLAudioElement): void {
  if (_currentAudio !== audio) return;
  publishSpeech({
    phase: audio.paused ? "paused" : "playing",
    currentTime: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
    duration: Number.isFinite(audio.duration) ? audio.duration : 0,
    rate: audio.playbackRate,
    seekable: Number.isFinite(audio.duration) && audio.duration > 0,
  });
}

export function toggleSpeakingPaused(): void {
  if (_currentAudio) {
    if (_currentAudio.paused) void _currentAudio.play();
    else _currentAudio.pause();
    publishAudioProgress(_currentAudio);
    return;
  }
  if (!_currentBrowserUtterance || typeof window === "undefined") return;
  if (window.speechSynthesis.paused) {
    window.speechSynthesis.resume();
    publishSpeech({ phase: "playing" });
  } else {
    window.speechSynthesis.pause();
    publishSpeech({ phase: "paused" });
  }
}

export function seekSpeaking(seconds: number): void {
  const audio = _currentAudio;
  if (!audio || !Number.isFinite(audio.duration)) return;
  audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + seconds));
  publishAudioProgress(audio);
}

export function setSpeakingRate(rate: number): void {
  const next = Math.max(0.5, Math.min(2, rate));
  if (_currentAudio) {
    _currentAudio.playbackRate = next;
    publishAudioProgress(_currentAudio);
  } else {
    if (_currentBrowserUtterance) _currentBrowserUtterance.rate = next;
    publishSpeech({ rate: next });
  }
}

// Interrompe qualquer fala em curso (servidor ou navegador). Usado quando o usuário
// volta a falar (barge-in) ou encerra o modo voz.
export function stopSpeaking(): void {
  _speechGeneration += 1;
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    try { window.speechSynthesis.cancel(); } catch { /* noop */ }
  }
  const finishBrowserSpeech = _currentBrowserResolve;
  _currentBrowserResolve = null;
  _currentBrowserUtterance = null;
  finishBrowserSpeech?.();
  if (_currentAudio) {
    try { _currentAudio.pause(); } catch { /* noop */ }
    const finishAudio = _currentAudioFinish;
    _currentAudioFinish = null;
    finishAudio?.();
  }
  clearProgressTimer();
  publishSpeech({ ...IDLE_SPEECH });
}

// Converte texto em fala (TTS) e toca o áudio. Servidor primeiro (voz local/
// OpenAI); qualquer falha cai na voz do navegador. Resolve quando a fala TERMINA
// (não só quando começa) — o modo voz espera isso antes de voltar a ouvir.
export async function speak(text: string, voice?: string): Promise<void> {
  // Cancela uma leitura anterior e guarda a geração desta chamada. Se o usuário
  // apertar Parar enquanto o TTS ainda está baixando, os bytes não começam a tocar.
  stopSpeaking();
  const generation = _speechGeneration;
  publishSpeech({ ...IDLE_SPEECH, phase: "loading" });
  try {
    const res = await fetch(`${API_URL}/voice/tts`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "TTS falhou");
    const blob = await res.blob();
    if (generation !== _speechGeneration) return;
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    _currentAudio = audio;
    await new Promise<void>((resolve, reject) => {
      let finished = false;
      const finish = (error?: unknown) => {
        if (finished) return;
        finished = true;
        clearProgressTimer();
        URL.revokeObjectURL(url);
        if (_currentAudio === audio) _currentAudio = null;
        if (_currentAudioFinish === finish) _currentAudioFinish = null;
        publishSpeech({ ...IDLE_SPEECH });
        if (error) reject(error); else resolve();
      };
      _currentAudioFinish = finish;
      audio.onloadedmetadata = () => publishAudioProgress(audio);
      audio.ontimeupdate = () => publishAudioProgress(audio);
      audio.onplay = () => publishAudioProgress(audio);
      audio.onpause = () => publishAudioProgress(audio);
      audio.onended = () => finish();
      audio.onerror = () => finish(new Error("Falha ao reproduzir o áudio"));
      _progressTimer = setInterval(() => publishAudioProgress(audio), 250);
      audio.play().catch(finish);
    });
  } catch {
    if (generation !== _speechGeneration) return;
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
