// Captura de fala com VAD (voice activity detection) leve para o "modo voz".
//
// Grava o microfone via MediaRecorder e mede o volume em tempo real com um
// AnalyserNode (Web Audio). Encerra sozinho quando o usuário PARA de falar
// (silêncio contínuo por `silenceMs` depois de ter começado a falar) — sem isso o
// usuário teria que clicar "parar" a cada frase. Também respeita um teto duro de
// duração e um timeout caso nenhuma fala seja detectada.

export interface UtteranceOptions {
  /** silêncio contínuo (ms) após fala que encerra a captura */
  silenceMs?: number;
  /** teto duro de duração (ms) */
  maxMs?: number;
  /** se nenhuma fala for detectada nesse tempo (ms), encerra sem resultado */
  startTimeoutMs?: number;
  /** nível de volume 0..1 a cada frame (para o HUD animar) */
  onLevel?: (level: number) => void;
  /** disparado na primeira vez que detecta fala */
  onSpeechStart?: () => void;
}

export interface Utterance {
  /** áudio capturado; `null` se cancelado ou se nenhuma fala foi detectada */
  done: Promise<Blob | null>;
  /** finaliza a captura agora e resolve com o que foi gravado */
  stop: () => void;
  /** aborta sem resultado (resolve `null`) */
  cancel: () => void;
}

const START_LEVEL = 0.035; // acima disso = fala começou
const VOICE_LEVEL = 0.02; // acima disso = ainda há voz (reinicia o timer de silêncio)

export async function captureUtterance(opts: UtteranceOptions = {}): Promise<Utterance> {
  const silenceMs = opts.silenceMs ?? 1500;
  const maxMs = opts.maxMs ?? 30_000;
  const startTimeoutMs = opts.startTimeoutMs ?? 8_000;

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const rec = new MediaRecorder(stream);
  const chunks: BlobPart[] = [];
  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };

  const AudioCtx = (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext);
  const ctx = new AudioCtx();
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);

  let raf = 0;
  let finished = false;
  let speechStarted = false;
  const started = performance.now();
  let lastVoice = started;

  let resolveDone!: (b: Blob | null) => void;
  const done = new Promise<Blob | null>((r) => { resolveDone = r; });

  const cleanup = () => {
    cancelAnimationFrame(raf);
    try { source.disconnect(); } catch { /* noop */ }
    try { ctx.close(); } catch { /* noop */ }
    stream.getTracks().forEach((t) => t.stop());
  };

  const finish = (keep: boolean) => {
    if (finished) return;
    finished = true;
    const emit = (blob: Blob | null) => { cleanup(); resolveDone(blob); };
    if (keep && speechStarted && rec.state !== "inactive") {
      rec.onstop = () => emit(new Blob(chunks, { type: "audio/webm" }));
      try { rec.stop(); } catch { emit(null); }
    } else {
      try { if (rec.state !== "inactive") rec.stop(); } catch { /* noop */ }
      emit(null);
    }
  };

  const tick = () => {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const level = Math.sqrt(sum / buf.length);
    opts.onLevel?.(Math.min(1, level * 4));
    const now = performance.now();
    if (level > START_LEVEL && !speechStarted) {
      speechStarted = true;
      opts.onSpeechStart?.();
    }
    if (level > VOICE_LEVEL) lastVoice = now;
    if (speechStarted && now - lastVoice > silenceMs) return finish(true);
    if (!speechStarted && now - started > startTimeoutMs) return finish(false);
    if (now - started > maxMs) return finish(true);
    raf = requestAnimationFrame(tick);
  };

  rec.start();
  raf = requestAnimationFrame(tick);

  return {
    done,
    stop: () => finish(true),
    cancel: () => finish(false),
  };
}
