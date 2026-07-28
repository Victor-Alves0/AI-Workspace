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

export type WakeEngine = "porcupine" | "vosk";

export interface WakeOptions {
  engine: WakeEngine;
  /** frase de ativação (Vosk casa por substring; Porcupine usa como label) */
  callName?: string;
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
  return opts.engine === "vosk"
    ? startVosk(opts, onWake)
    : startPorcupine(opts, onWake);
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
