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

export type WakeEngine = "porcupine" | "vosk" | "whisper" | "openwakeword";

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
  /** OpenWakeWord: URL do modelo treinado (.onnx) + os 2 compartilhados + limiar */
  owwModelUrl?: string;
  owwMelspecUrl?: string;
  owwEmbeddingUrl?: string;
  owwThreshold?: number;
  onError?: (msg: string) => void;
  onReady?: () => void;
  /** Vosk: transcript reconhecido ao vivo (parcial/final), casando a palavra ou não.
   *  Serve para o "Testar escuta" mostrar o que foi entendido. Porcupine não transcreve. */
  onPartial?: (text: string) => void;
  /** OpenWakeWord: score do modelo (0..1) a cada passo — para o teste mostrar/calibrar. */
  onScore?: (score: number) => void;
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
  if (opts.engine === "openwakeword") return startOpenWakeWord(opts, onWake);
  return startPorcupine(opts, onWake);
}

// URLs padrão dos 2 modelos COMPARTILHADOS do OpenWakeWord (melspectrograma +
// embedding). Precisam de CORS liberado; o usuário pode trocar por um espelho
// próprio se estas falharem. O modelo de wake em si é treinado pelo usuário.
export const OWW_MELSPEC_DEFAULT =
  "https://huggingface.co/onnx-community/openwakeword/resolve/main/melspectrogram.onnx";
export const OWW_EMBEDDING_DEFAULT =
  "https://huggingface.co/onnx-community/openwakeword/resolve/main/embedding_model.onnx";

// Pré-carrega/valida os 3 modelos ONNX do OpenWakeWord (sem mic). Usado pelo
// "Confirmar modelo" e reaproveitado pela escuta. Lança em erro (URL/CORS/formato).
export async function loadOwwModels(
  modelUrl: string, melspecUrl?: string, embeddingUrl?: string,
): Promise<void> {
  await getOwwSessions(modelUrl, melspecUrl || OWW_MELSPEC_DEFAULT, embeddingUrl || OWW_EMBEDDING_DEFAULT);
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

// --------------------------------------------------------------------------- //
// OpenWakeWord (onnxruntime-web) — wake word TREINADA pelo usuário, on-device.
// Pipeline de 3 modelos ONNX: áudio → melspectrograma → embedding → modelo de
// wake (score 0..1). Os 2 primeiros são compartilhados (padrão OWW_*_DEFAULT); o
// 3º é o .onnx que o usuário treina (Colab do openWakeWord) e hospeda.
//
// Estratégia ROBUSTA: em vez de bookkeeping incremental de frames (frágil e não
// verificável), a cada 80 ms recomputamos o pipeline sobre uma janela DESLIZANTE
// de ~2 s e pegamos os últimos frames — o campo receptivo do modelo é ~2 s (16
// embeddings × passo 8 × 76 mel-frames). Custa um pouco mais de CPU, mas é
// simples e correto (STFT é local; pegar os últimos frames evita a borda inicial).
// Constantes do openWakeWord: janela 76 mel-frames, passo 8, 16 embeddings.
// --------------------------------------------------------------------------- //
const OWW_MEL_WINDOW = 76;   // mel-frames por embedding
const OWW_MEL_STEP = 8;      // passo entre janelas de embedding
const OWW_N_EMB = 16;        // embeddings que o modelo de wake espera
const OWW_MEL_NEEDED = OWW_MEL_WINDOW + (OWW_N_EMB - 1) * OWW_MEL_STEP; // 196
const OWW_SR = 16000;
const OWW_AUDIO_WINDOW = OWW_SR * 2; // ~2 s de áudio no buffer deslizante

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type OrtSession = any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let _ort: any = null;
const _owwCache = new Map<string, Promise<OrtSession>>();

// eslint-disable-next-line @typescript-eslint/no-explicit-any
// versão pinada no package.json — usada no caminho do wasm do CDN
const ORT_VERSION = "1.14.0";

async function getOrt(): Promise<any> {
  if (!_ort) {
    _ort = await import("onnxruntime-web");
    // wasm servido pelo CDN (evita ter que emitir os .wasm no build do Next). Sem
    // CSP no app, o fetch cross-origin é permitido. Usa a versão detectada se houver,
    // senão a pinada. Single-thread (sem SharedArrayBuffer/COOP-COEP no app).
    try {
      const v = _ort.env?.versions?.common || _ort.version || ORT_VERSION;
      _ort.env.wasm.wasmPaths = `https://cdn.jsdelivr.net/npm/onnxruntime-web@${v}/dist/`;
      _ort.env.wasm.numThreads = 1;
    } catch { /* usa o default do ort */ }
  }
  return _ort;
}

function _session(url: string): Promise<OrtSession> {
  let p = _owwCache.get(url);
  if (!p) {
    p = (async () => {
      const ort = await getOrt();
      const buf = await fetch(url).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} ao baixar ${url}`);
        return r.arrayBuffer();
      });
      return ort.InferenceSession.create(new Uint8Array(buf));
    })();
    _owwCache.set(url, p);
  }
  return p;
}

async function getOwwSessions(modelUrl: string, melspecUrl: string, embeddingUrl: string) {
  if (!modelUrl) throw new Error("Informe a URL do seu modelo OpenWakeWord (.onnx).");
  const [mel, emb, wake] = await Promise.all([
    _session(melspecUrl), _session(embeddingUrl), _session(modelUrl),
  ]);
  return { mel, emb, wake };
}

async function startOpenWakeWord(opts: WakeOptions, onWake: () => void): Promise<WakeHandle> {
  const ort = await getOrt();
  const { mel, emb, wake } = await getOwwSessions(
    opts.owwModelUrl || "",
    opts.owwMelspecUrl || OWW_MELSPEC_DEFAULT,
    opts.owwEmbeddingUrl || OWW_EMBEDDING_DEFAULT,
  );
  const threshold = typeof opts.owwThreshold === "number" ? opts.owwThreshold : 0.5;
  const melIn = mel.inputNames[0], melOut = mel.outputNames[0];
  const embIn = emb.inputNames[0], embOut = emb.outputNames[0];
  const wakeIn = wake.inputNames[0], wakeOut = wake.outputNames[0];
  opts.onReady?.();

  let paused = false;
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let node: ScriptProcessorNode | null = null;
  const ring = new Float32Array(OWW_AUDIO_WINDOW); // buffer deslizante (mono, -1..1)
  let filled = 0;         // quantas amostras válidas já entraram (satura no tamanho)
  let sinceRun = 0;       // amostras acumuladas desde a última inferência
  let busy = false;
  let cooldown = 0;       // ignora disparos por um tempo após acionar (anti-repetição)

  const pushAudio = (data: Float32Array) => {
    // desloca o ring e anexa as novas amostras no fim
    if (data.length >= ring.length) {
      ring.set(data.subarray(data.length - ring.length));
    } else {
      ring.copyWithin(0, data.length);
      ring.set(data, ring.length - data.length);
    }
    filled = Math.min(ring.length, filled + data.length);
  };

  const infer = async () => {
    if (busy) return;
    busy = true;
    try {
      // 1) melspectrograma sobre a janela de áudio (escala p/ int16, como o OWW)
      const audio = new Float32Array(ring.length);
      for (let i = 0; i < ring.length; i++) audio[i] = ring[i] * 32767;
      const melRes = await mel.run({ [melIn]: new ort.Tensor("float32", audio, [1, audio.length]) });
      const mt = melRes[melOut];                 // [1,1,F,32]
      const F = mt.dims[mt.dims.length - 2] as number;
      const B = mt.dims[mt.dims.length - 1] as number; // 32
      const md = mt.data as Float32Array;
      if (F < OWW_MEL_NEEDED) return;            // ainda sem 2 s de áudio
      // pega os ÚLTIMOS OWW_MEL_NEEDED frames e aplica a normalização do OWW (/10+2)
      const start = F - OWW_MEL_NEEDED;
      // 2) monta as OWW_N_EMB janelas [76,32] → batch [16,76,32,1]
      const batch = new Float32Array(OWW_N_EMB * OWW_MEL_WINDOW * B);
      let o = 0;
      for (let w = 0; w < OWW_N_EMB; w++) {
        const base = (start + w * OWW_MEL_STEP) * B;
        for (let k = 0; k < OWW_MEL_WINDOW * B; k++) batch[o++] = md[base + k] / 10 + 2;
      }
      const embRes = await emb.run({ [embIn]: new ort.Tensor("float32", batch, [OWW_N_EMB, OWW_MEL_WINDOW, B, 1]) });
      const ed = embRes[embOut].data as Float32Array; // [16,1,1,96] → 16×96
      const D = ed.length / OWW_N_EMB;                 // 96
      // 3) modelo de wake sobre [1,16,96]
      const wakeRes = await wake.run({ [wakeIn]: new ort.Tensor("float32", ed, [1, OWW_N_EMB, D]) });
      const score = (wakeRes[wakeOut].data as Float32Array)[0] ?? 0;
      opts.onScore?.(score);
      if (!paused && cooldown <= 0 && score >= threshold) {
        cooldown = Math.ceil(OWW_SR * 1.5); // ~1,5 s de silêncio antes de re-disparar
        onWake();
      }
    } catch (e) {
      opts.onError?.(String(e));
    } finally {
      busy = false;
    }
  };

  const onFrame = (e: AudioProcessingEvent) => {
    if (paused) return;
    const data = e.inputBuffer.getChannelData(0);
    pushAudio(new Float32Array(data)); // cópia: o inputBuffer é reusado
    if (cooldown > 0) cooldown -= data.length;
    sinceRun += data.length;
    if (filled >= OWW_AUDIO_WINDOW && sinceRun >= 1280) { // a cada ~80 ms
      sinceRun = 0;
      void infer();
    }
  };

  const startMic = async () => {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    ctx = new AC({ sampleRate: OWW_SR });
    const src = ctx.createMediaStreamSource(stream);
    // bufferSize precisa ser potência de 2; a cadência de ~80 ms vem do acumulador
    // `sinceRun` (>=1280 amostras), não do tamanho do bloco.
    node = ctx.createScriptProcessor(2048, 1, 1);
    node.onaudioprocess = onFrame;
    src.connect(node);
    node.connect(ctx.destination); // dispara o processamento (saída muda)
  };

  const stopMic = () => {
    try { node?.disconnect(); } catch { /* noop */ }
    try { ctx?.close(); } catch { /* noop */ }
    stream?.getTracks().forEach((t) => t.stop());
    node = null; ctx = null; stream = null; filled = 0; sinceRun = 0; cooldown = 0;
  };

  await startMic();

  return {
    pause: async () => { if (paused) return; paused = true; stopMic(); },
    resume: async () => { if (!paused) return; paused = false; try { await startMic(); } catch (e) { opts.onError?.(String(e)); } },
    stop: async () => { paused = true; stopMic(); },
  };
}
