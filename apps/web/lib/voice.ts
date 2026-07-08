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

// Converte texto em fala (TTS) e toca o áudio.
export async function speak(text: string, voice?: string): Promise<void> {
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
  audio.onended = () => URL.revokeObjectURL(url);
  await audio.play();
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
