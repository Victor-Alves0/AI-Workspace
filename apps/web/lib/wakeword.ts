// Wake word ("hey nome") ON-DEVICE, com dois engines selecionáveis. Tudo por
// DYNAMIC IMPORT: as libs WASM (pesadas) só baixam quando a escuta é ligada, então
// não incham o bundle base nem afetam quem não usa.
//
//  - Porcupine (Picovoice): preciso e leve; precisa de uma AccessKey (grátis) e do
//    modelo `porcupine_params.pv` (empacotado em /wake/). Keyword embutida
//    (JARVIS/COMPUTER/…) ou custom `.ppn`.
//  - Vosk (open-source): offline, sem chave, nome arbitrário; baixa um modelo
//    (~40MB) de uma URL configurável (cacheado pelo navegador). Casa o `callName`
//    no que reconhece.
//
// Ambos processam o áudio LOCALMENTE — nada é enviado a servidor. O chamador
// (chat/page) pausa a escuta enquanto o modo voz usa o mic e retoma depois.

export type WakeEngine = "porcupine" | "vosk" | "whisper";

export interface WakeOptions {
  engine: WakeEngine;
  /** frase de ativação (Vosk/Whisper casam por substring; Porcupine usa como label) */
  callName?: string;
  /** Whisper: id do modelo transformers.js (default Xenova/whisper-base) */
  whisperModel?: string;
  /** Porcupine: AccessKey da Picovoice (por-usuário) */
  accessKey?: string;
  /** Porcupine: keyword embutida (ex.: "JARVIS") OU URL de um .ppn custom */
  porcupineKeyword?: string;
  /** Porcupine: caminho do modelo params (default /wake/porcupine_params.pv) */
  porcupineModelPath?: string;
  /** Vosk: URL do modelo (.tar.gz/.zip) */
  voskModelUrl?: string;
  onError?: (msg: string) => void;
  onReady?: () => void;
  /** Vosk: transcript reconhecido ao vivo (parcial/final), casando a palavra ou não.
   *  Serve para o "Testar escuta" mostrar o que foi entendido. Porcupine não transcreve. */
  onPartial?: (text: string) => void;
}

export interface WakeHandle {
  stop: () => Promise<void>;
  /** solta o microfone (usado enquanto o modo voz grava) */
  pause: () => Promise<void>;
  /** retoma a escuta após o modo voz */
  resume: () => Promise<void>;
}

// normaliza p/ comparar keyword do usuário com os valores do enum ("Hey Google",
// "Jarvis"…): tira tudo que não é letra/dígito e baixa a caixa. Assim "jarvis",
// "JARVIS", "hey_google" e "Hey Google" casam com o valor certo do BuiltInKeyword.
const normKw = (s: string) => s.replace(/[^a-z0-9]/gi, "").toLowerCase();

export async function startWakeWord(opts: WakeOptions, onWake: () => void): Promise<WakeHandle> {
  if (opts.engine === "vosk") return startVosk(opts, onWake);
  if (opts.engine === "whisper") return startWhisper(opts, onWake);
  return startPorcupine(opts, onWake);
}

// Baixa e carrega um modelo Vosk só para CONFIRMAR que a URL funciona (CORS,
// formato, download). Não usa mic. Lança em caso de erro (URL/CORS/formato).
export async function loadVoskModel(url: string): Promise<void> {
  const { createModel } = await import("vosk-browser");
  const model = await createModel(url);
  try { (model as unknown as { terminate?: () => void }).terminate?.(); } catch { /* noop */ }
}

// Pré-carrega o modelo Whisper (baixa do HF na 1ª vez, depois cacheia no
// navegador). Usado pelo "Confirmar modelo" e reaproveitado pela escuta.
export async function loadWhisperModel(model?: string): Promise<void> {
  await getWhisperPipe(model || WHISPER_DEFAULT);
}

export const WHISPER_DEFAULT = "Xenova/whisper-base";

// STT LOCAL: transcreve um blob de áudio (webm/opus do MediaRecorder) inteiramente
// no navegador com o Whisper — sem servidor, sem chave. Decodifica → reamostra p/
// 16 kHz mono → pipeline. Reaproveita o modelo já baixado da wake word.
export async function transcribeWhisper(blob: Blob, model?: string): Promise<string> {
  const asr = await getWhisperPipe(model || WHISPER_DEFAULT);
  const arr = await blob.arrayBuffer();
  const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ac = new AC();
  let decoded: AudioBuffer;
  try { decoded = await ac.decodeAudioData(arr); } finally { try { await ac.close(); } catch { /* noop */ } }
  // reamostra p/ 16 kHz mono via OfflineAudioContext
  const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * 16000)), 16000);
  const src = off.createBufferSource();
  src.buffer = decoded;
  src.connect(off.destination);
  src.start();
  const rendered = await off.startRendering();
  const audio = rendered.getChannelData(0).slice();
  const out = await asr(audio);
  return String((out?.text ?? "")).trim();
}

// --------------------------------------------------------------------------- //
// Porcupine
// --------------------------------------------------------------------------- //
async function startPorcupine(opts: WakeOptions, onWake: () => void): Promise<WakeHandle> {
  if (!opts.accessKey) throw new Error("Porcupine precisa de uma AccessKey da Picovoice (Configurações do modelo → Voz).");
  const { PorcupineWorker, BuiltInKeyword } = await import("@picovoice/porcupine-web");
  const { WebVoiceProcessor } = await import("@picovoice/web-voice-processor");

  const kwRaw = (opts.porcupineKeyword || "Jarvis").trim();
  // Os VALORES do enum são as keywords válidas ("Jarvis", "Hey Google"…); casa
  // por forma normalizada (as CHAVES são PascalCase, então lookup direto falha).
  const builtin = (Object.values(BuiltInKeyword) as string[]).find((v) => normKw(v) === normKw(kwRaw));
  let keyword: unknown;
  if (builtin) {
    keyword = builtin; // passa o valor do enum direto (o create aceita a string)
  } else {
    // URL/caminho de um .ppn custom (gerado no console Picovoice)
    keyword = { publicPath: kwRaw, label: opts.callName || "wake" };
  }
  const model = { publicPath: opts.porcupineModelPath || "/wake/porcupine_params.pv" };

  let paused = false;
  const worker = await (PorcupineWorker as unknown as {
    create: (k: string, kw: unknown, cb: () => void, m: unknown) => Promise<{ release: () => Promise<void>; terminate: () => void }>;
  }).create(
    opts.accessKey,
    keyword,
    () => { if (!paused) onWake(); },
    model,
  );
  await WebVoiceProcessor.subscribe(worker as never);
  opts.onReady?.();

  return {
    // idempotentes: pausar já-pausado ou retomar já-ativo é no-op (senão o
    // subscribe/unsubscribe duplicaria callbacks e chamadas de mic).
    pause: async () => { if (paused) return; paused = true; try { await WebVoiceProcessor.unsubscribe(worker as never); } catch { /* noop */ } },
    resume: async () => { if (!paused) return; paused = false; try { await WebVoiceProcessor.subscribe(worker as never); } catch { /* noop */ } },
    stop: async () => {
      paused = true;
      try { await WebVoiceProcessor.unsubscribe(worker as never); } catch { /* noop */ }
      try { await worker.release(); } catch { /* noop */ }
      try { worker.terminate(); } catch { /* noop */ }
    },
  };
}

// --------------------------------------------------------------------------- //
// Vosk (open-source)
// --------------------------------------------------------------------------- //
async function startVosk(opts: WakeOptions, onWake: () => void): Promise<WakeHandle> {
  const phrase = (opts.callName || "").trim().toLowerCase();
  if (!phrase) throw new Error("O modo Vosk precisa de um 'chamado/nome' para reconhecer.");
  if (!opts.voskModelUrl) throw new Error("Informe a URL do modelo Vosk (Configurações do modelo → Voz).");
  const { createModel } = await import("vosk-browser");

  const model = await createModel(opts.voskModelUrl);
  opts.onReady?.();

  let paused = false;
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let node: ScriptProcessorNode | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let rec: any = null;

  const match = (text: string) => {
    if (paused || !text) return;
    if (text.toLowerCase().includes(phrase)) onWake();
  };

  const startMic = async () => {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    ctx = new AC();
    rec = new (model as unknown as { KaldiRecognizer: new (n: number) => any }).KaldiRecognizer(ctx.sampleRate); // eslint-disable-line @typescript-eslint/no-explicit-any
    rec.on("result", (m: { result?: { text?: string } }) => { const t = m?.result?.text || ""; if (t) opts.onPartial?.(t); match(t); });
    rec.on("partialresult", (m: { result?: { partial?: string } }) => { const t = m?.result?.partial || ""; if (t) opts.onPartial?.(t); match(t); });
    const source = ctx.createMediaStreamSource(stream);
    node = ctx.createScriptProcessor(4096, 1, 1);
    node.onaudioprocess = (e) => { try { rec.acceptWaveform(e.inputBuffer); } catch { /* frame ruim */ } };
    source.connect(node);
    node.connect(ctx.destination);
  };

  const stopMic = () => {
    try { node?.disconnect(); } catch { /* noop */ }
    try { ctx?.close(); } catch { /* noop */ }
    stream?.getTracks().forEach((t) => t.stop());
    try { rec?.remove?.(); } catch { /* noop */ }
    node = null; ctx = null; stream = null; rec = null;
  };

  await startMic();

  return {
    // idempotentes: retomar quando NÃO está pausado seria um segundo startMic()
    // sem soltar o primeiro → dois streams/reconhecedores vivos (vazamento de mic).
    pause: async () => { if (paused) return; paused = true; stopMic(); },
    resume: async () => { if (!paused) return; paused = false; try { await startMic(); } catch (e) { opts.onError?.(String(e)); } },
    stop: async () => {
      paused = true;
      stopMic();
      try { (model as unknown as { terminate?: () => void }).terminate?.(); } catch { /* noop */ }
    },
  };
}

// --------------------------------------------------------------------------- //
// Whisper (transformers.js) — ASR on-device, MELHOR com nomes que o Vosk pequeno.
// Roda o Whisper por TRECHO de fala (gate por VAD de energia): quando você para de
// falar, transcreve o trecho e casa a palavra. On-device; o modelo baixa do HF na
// 1ª vez e fica no cache do navegador (offline depois).
// --------------------------------------------------------------------------- //
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let _whisperPipe: Promise<any> | null = null;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function getWhisperPipe(model: string): Promise<any> {
  if (!_whisperPipe) {
    _whisperPipe = (async () => {
      const { pipeline, env } = await import("@xenova/transformers");
      env.allowLocalModels = false; // busca do HF CDN (cacheado pelo navegador)
      return pipeline("automatic-speech-recognition", model);
    })();
  }
  return _whisperPipe;
}

// Reamostra PCM mono para 16 kHz (interpolação linear — suficiente p/ o gate de
// fala do Whisper). Blinda contra navegadores que IGNORAM `sampleRate: 16000` no
// AudioContext (ex.: Safari antigo) e entregam o áudio na taxa do hardware.
function resampleTo16k(data: Float32Array, fromRate: number): Float32Array {
  if (fromRate === 16000 || data.length === 0) return data;
  const ratio = fromRate / 16000;
  const outLen = Math.max(1, Math.round(data.length / ratio));
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, data.length - 1);
    const frac = pos - i0;
    out[i] = data[i0] * (1 - frac) + data[i1] * frac;
  }
  return out;
}

async function startWhisper(opts: WakeOptions, onWake: () => void): Promise<WakeHandle> {
  const phrase = (opts.callName || "").trim().toLowerCase();
  if (!phrase) throw new Error("O modo Whisper precisa de um 'nome' para reconhecer.");
  const asr = await getWhisperPipe(opts.whisperModel || WHISPER_DEFAULT);
  opts.onReady?.();

  const SR = 16000;          // Whisper espera 16 kHz mono
  const START = 0.02;        // acima disso = fala
  const SILENCE_MS = 600;    // silêncio que fecha o trecho
  let maxSamples = SR * 5;   // teto de ~5s por trecho (ajustado à taxa real do ctx)

  let paused = false;
  let ctx: AudioContext | null = null;
  let srActual = SR;         // taxa REAL do contexto (pode diferir de SR se ignorada)
  let stream: MediaStream | null = null;
  let node: ScriptProcessorNode | null = null;
  let chunks: Float32Array[] = [];
  let total = 0;
  let speaking = false;
  let lastVoice = 0;
  let busy = false;

  const flush = async () => {
    speaking = false;
    if (busy || total === 0) { chunks = []; total = 0; return; }
    busy = true;
    const raw = new Float32Array(total);
    let off = 0;
    for (const c of chunks) { raw.set(c, off); off += c.length; }
    chunks = []; total = 0;
    const audio = resampleTo16k(raw, srActual);
    try {
      const out = await asr(audio);
      const text = String((out?.text ?? "")).trim();
      if (text) opts.onPartial?.(text);
      if (!paused && text && text.toLowerCase().includes(phrase)) onWake();
    } catch (e) { opts.onError?.(String(e)); }
    busy = false;
  };

  const onFrame = (e: AudioProcessingEvent) => {
    if (paused) return;
    const data = e.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const level = Math.sqrt(sum / data.length);
    const now = performance.now();
    if (level > START) { speaking = true; lastVoice = now; }
    if (speaking) {
      chunks.push(new Float32Array(data)); // cópia: o inputBuffer é reusado
      total += data.length;
      if (total >= maxSamples || now - lastVoice > SILENCE_MS) void flush();
    }
  };

  const startMic = async () => {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    ctx = new AC({ sampleRate: SR });
    srActual = ctx.sampleRate;       // taxa efetiva (o navegador pode ignorar a dica)
    maxSamples = Math.round(srActual * 5);
    const src = ctx.createMediaStreamSource(stream);
    node = ctx.createScriptProcessor(4096, 1, 1);
    node.onaudioprocess = onFrame;
    src.connect(node);
    node.connect(ctx.destination); // necessário p/ disparar o processamento (saída fica muda)
  };

  const stopMic = () => {
    try { node?.disconnect(); } catch { /* noop */ }
    try { ctx?.close(); } catch { /* noop */ }
    stream?.getTracks().forEach((t) => t.stop());
    node = null; ctx = null; stream = null; chunks = []; total = 0; speaking = false;
  };

  await startMic();

  return {
    pause: async () => { if (paused) return; paused = true; stopMic(); },
    resume: async () => { if (!paused) return; paused = false; try { await startMic(); } catch (e) { opts.onError?.(String(e)); } },
    stop: async () => { paused = true; stopMic(); },
  };
}
