/** Keep the browser's real recording format (Safari may produce MP4). */
export function recordingBlob(chunks: Blob[], mimeType: string): Blob {
  return new Blob(chunks, { type: mimeType || chunks.find((chunk) => chunk.type)?.type || "audio/webm" });
}

export function recordingFilename(blob: Blob): string {
  const formats: Record<string, string> = {
    "audio/mp4": "m4a", "video/mp4": "mp4", "audio/mpeg": "mp3",
    "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/flac": "flac", "audio/aac": "aac", "audio/webm": "webm",
  };
  return `audio.${formats[blob.type.split(";", 1)[0].toLowerCase()] || "webm"}`;
}

/** Extensões de áudio reconhecidas mesmo quando o navegador não informa o MIME
 *  (acontece com .opus/.amr/.wma no Windows). */
export const AUDIO_EXT_RE = /\.(mp3|wav|wave|m4a|mp4a|aac|ogg|oga|opus|flac|webm|weba|amr|awb|wma|aif|aiff|aifc|caf|3gp|3ga|mka|mpga|mp2|ac3|spx|ra|au|snd)$/i;

export function isAudioFile(f: { type?: string; name?: string }): boolean {
  return (f.type || "").startsWith("audio/") || AUDIO_EXT_RE.test(f.name || "");
}

/** Formatos que os modelos com áudio nativo aceitam de forma comum; o resto é
 *  convertido para WAV antes de enviar. */
const NATIVE_OK = /\.(mp3|wav|wave)$/i;

/** Converte qualquer áudio que o navegador saiba decodificar para WAV (16 kHz, mono,
 *  16-bit) — formato que todo modelo com áudio nativo aceita. Devolve o original se
 *  já for compatível ou se o navegador não decodificar o formato. */
export async function toModelAudio(file: File): Promise<File> {
  if (NATIVE_OK.test(file.name) || /^audio\/(mpeg|mp3|wav|x-wav|wave)$/i.test(file.type)) return file;
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return file;
    const ctx = new Ctx();
    let decoded: AudioBuffer;
    try {
      decoded = await ctx.decodeAudioData(await file.arrayBuffer());
    } finally {
      void ctx.close().catch(() => {});
    }
    const rate = 16000;
    const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * rate)), rate);
    const src = off.createBufferSource();
    src.buffer = decoded;
    src.connect(off.destination);
    src.start();
    const mono = (await off.startRendering()).getChannelData(0);
    const wav = encodeWav(mono, rate);
    const base = (file.name || "audio").replace(/\.[^.]+$/, "");
    return new File([wav], `${base}.wav`, { type: "audio/wav" });
  } catch {
    return file; // formato que o navegador não decodifica: o servidor tenta converter
  }
}

function encodeWav(samples: Float32Array, rate: number): ArrayBuffer {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  return buf;
}
