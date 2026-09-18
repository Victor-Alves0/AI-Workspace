"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Loader2, Volume2, VolumeX } from "lucide-react";
import { api, API_URL, ApiError } from "@/lib/api";

/** true só na resposta que está sendo GERADA e com "tocar automaticamente" ligado —
 *  reabrir um chat antigo nunca dispara uma rajada de sons. */
export const SoundAutoplayContext = createContext(false);

// descrição → URL do som já gerado (evita repetir a requisição na mesma sessão)
const urls = new Map<string, string>();
// um som só toca sozinho uma vez: o Markdown do streaming remonta o botão quando o
// parágrafo passa de "cauda" para "estável", e sem isto o mesmo som tocaria de novo
const autoplayed = new Map<string, number>();
const AUTOPLAY_WINDOW_MS = 15_000;

// só um som por vez: o novo corta o anterior, como numa mesa de efeitos
let current: HTMLAudioElement | null = null;

type State = "idle" | "loading" | "playing" | "error";

async function resolveUrl(prompt: string): Promise<string> {
  const known = urls.get(prompt);
  if (known) return known;
  const { url } = await api.post<{ url: string; cached: boolean }>("/sfx", { prompt });
  const full = url.startsWith("http") ? url : API_URL + url;
  urls.set(prompt, full);
  return full;
}

/** Botão inline de efeito sonoro, no meio da narração. O som só é gerado no primeiro
 *  toque (e fica em cache no servidor): narração que ninguém ouve não gasta crédito. */
export default function SoundChip({ prompt, label }: { prompt: string; label: string }) {
  const autoplay = useContext(SoundAutoplayContext);
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

  async function play() {
    if (state === "loading") return;
    if (state === "playing" && current) {
      current.pause();
      current = null;
      setState("idle");
      return;
    }
    setState("loading");
    setError(null);
    try {
      const src = await resolveUrl(prompt);
      current?.pause();
      const audio = new Audio(src);
      current = audio;
      audio.onended = () => { if (mounted.current) setState("idle"); if (current === audio) current = null; };
      audio.onerror = () => { if (mounted.current) { setState("error"); setError("Não foi possível tocar o som."); } };
      await audio.play();
      if (mounted.current) setState("playing");
    } catch (err) {
      if (!mounted.current) return;
      setState("error");
      setError(err instanceof ApiError ? err.message : "Não foi possível gerar o som.");
    }
  }

  useEffect(() => {
    if (!autoplay) return;
    const last = autoplayed.get(prompt) ?? 0;
    if (Date.now() - last < AUTOPLAY_WINDOW_MS) return;
    autoplayed.set(prompt, Date.now());
    void play();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoplay, prompt]);

  const Icon = state === "loading" ? Loader2 : state === "error" ? VolumeX : Volume2;
  return (
    <button
      type="button"
      onClick={() => void play()}
      title={error ?? (state === "playing" ? "Parar som" : `Tocar: ${prompt}`)}
      aria-label={state === "playing" ? `Parar som: ${label}` : `Tocar som: ${label}`}
      aria-pressed={state === "playing"}
      className={`mx-0.5 inline-flex max-w-[16rem] translate-y-[-1px] items-center gap-1 rounded-full border px-2 py-0.5 align-middle text-[0.8em] font-medium leading-5 transition-colors ${
        state === "error"
          ? "border-rose-400/30 bg-rose-500/10 text-rose-300"
          : state === "playing"
            ? "border-violet-400/50 bg-violet-500/20 text-violet-100"
            : "border-violet-400/25 bg-violet-500/10 text-violet-200 hover:border-violet-400/50 hover:bg-violet-500/20"
      }`}
    >
      <Icon size={13} className={`shrink-0 ${state === "loading" ? "animate-spin" : state === "playing" ? "animate-pulse" : ""}`} />
      <span className="truncate">{label}</span>
    </button>
  );
}

// ------------------------------------------------------------------------- //
// Marcador no texto                                                           //
// ------------------------------------------------------------------------- //
// A IA escreve `[[som: descrição em inglês | rótulo]]` (ou `[[sfx: ...]]`). Viramos
// isso num link com protocolo próprio, que o renderizador de Markdown troca pelo
// botão — assim o som fica NO MEIO da frase, e não num bloco à parte.
const MARKER_RE = /\[\[\s*(?:som|sfx|sound)\s*:\s*([^\]|]{1,400}?)\s*(?:\|\s*([^\]]{1,120}?)\s*)?\]\]/gi;
// marcador de SOM ainda sendo escrito no fim do streaming ("[[", "[[so", "[[som: por…")
// — some até fechar. Só casa prefixos da palavra-chave: `[[artifact:` e `[[diagram`
// no fim continuam intocados.
const OPEN_TAIL_RE = /\[\[(?:\s*(?:s(?:o(?:m|u(?:n(?:d)?)?)?|f(?:x)?)?)(?:\s*:[^\]]*)?)?$/i;

export const SFX_PROTOCOL = "sfx:";

export function withSoundLinks(markdown: string, streaming: boolean): string {
  let out = markdown.replace(MARKER_RE, (_m, prompt: string, label?: string) => {
    const texto = (label || prompt).replace(/[[\]]/g, "").trim();
    // parênteses cru fecharia o link do Markdown no meio da descrição
    const alvo = encodeURIComponent(prompt.trim()).replace(/\(/g, "%28").replace(/\)/g, "%29");
    return `[${texto}](${SFX_PROTOCOL}${alvo})`;
  });
  if (streaming) out = out.replace(OPEN_TAIL_RE, "");
  return out;
}

export function soundPromptFromHref(href: string | undefined): string | null {
  if (!href || !href.startsWith(SFX_PROTOCOL)) return null;
  try {
    return decodeURIComponent(href.slice(SFX_PROTOCOL.length));
  } catch {
    return null;
  }
}
