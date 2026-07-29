"use client";

// Credenciais da WAKE WORD — configuração DO USUÁRIO (como chave de API), guardada
// em profile.wake e compartilhada por todos os modelos. O modelo só escolhe o
// engine e a palavra; aqui ficam a AccessKey da Picovoice, a URL do modelo Vosk e
// o .ppn custom. Inclui um "Testar escuta" que liga o mic de verdade e acende quando
// ouve a palavra — é assim que o usuário sabe que funciona.

import { useEffect, useRef, useState } from "react";
import { Ear, Loader2, Check, Mic, Download, Trash2 } from "lucide-react";
import {
  startWakeWord, loadVoskModel, loadWhisperModel, loadOwwModels,
  OWW_MELSPEC_DEFAULT, OWW_EMBEDDING_DEFAULT, type WakeHandle,
} from "@/lib/wakeword";
import { api } from "@/lib/api";
import type { WakeCreds } from "@/lib/types";

// modelo pequeno de PT-BR hospedado pela vosk-browser (CORS liberado); serve de
// default para o campo ficar utilizável sem caça ao link.
const VOSK_DEFAULT =
  "https://ccoreilly.github.io/vosk-browser/models/vosk-model-small-pt-0.3.tar.gz";

type TestState = "idle" | "loading" | "listening" | "heard" | "error";

export default function AssistantVoicePanel() {
  // Credenciais cifradas no servidor (UserSecret), buscadas/salvas por endpoint —
  // NÃO ficam no profile (texto claro) nem vazam no dump de backup.
  const [cfg, setCfg] = useState<WakeCreds>({});
  const cfgRef = useRef<WakeCreds>({});           // espelho do cfg p/ o patch ler o valor atual
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    api.get<WakeCreds>("/voice/wake").then((d) => { cfgRef.current = d ?? {}; setCfg(d ?? {}); }).catch(() => {});
  }, []);
  // limpa o save pendente ao desmontar (não dispara PUT depois de sair da tela)
  useEffect(() => () => { if (saveTimer.current) clearTimeout(saveTimer.current); }, []);

  // atualiza o estado e agenda o PUT (debounce) FORA do updater — sem efeito
  // colateral dentro do setState (evita PUT duplicado no StrictMode).
  const patch = (p: Partial<WakeCreds>) => {
    const next = { ...cfgRef.current, ...p };
    cfgRef.current = next;
    setCfg(next);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      api.put("/voice/wake", {
        picovoice_key: next.picovoice_key ?? "",
        ppn_url: next.ppn_url ?? "",
        vosk_model_url: next.vosk_model_url ?? "",
        oww_model_url: next.oww_model_url ?? "",
        oww_melspec_url: next.oww_melspec_url ?? "",
        oww_embedding_url: next.oww_embedding_url ?? "",
      }).catch(() => {});
    }, 600);
  };

  // Estado "instalado" dos modelos (marca no localStorage; Whisper também evita o
  // cache do transformers.js). Vosk guarda a URL instalada → detecta "mudou o link".
  const [whInstalled, setWhInstalled] = useState(false);
  const [voskInstalledUrl, setVoskInstalledUrl] = useState<string | null>(null);
  useEffect(() => {
    try {
      setWhInstalled(localStorage.getItem("aiw_wake_whisper") === "1");
      setVoskInstalledUrl(localStorage.getItem("aiw_wake_vosk"));
    } catch { /* localStorage indisponível */ }
  }, []);
  const evictTransformers = async () => {
    try {
      const ks = await caches.keys();
      for (const k of ks) if (/transformers/i.test(k)) await caches.delete(k);
    } catch { /* Cache API indisponível */ }
  };

  // Modelo Vosk que efetivamente será usado (o do link, ou o padrão quando vazio),
  // e um nome curto derivado da URL para mostrar ao usuário qual está valendo.
  const usingDefaultVosk = !(cfg.vosk_model_url || "").trim();
  const effVoskUrl = usingDefaultVosk ? VOSK_DEFAULT : (cfg.vosk_model_url as string).trim();
  const voskName = (u: string) => {
    try { return (new URL(u).pathname.split("/").pop() || u).replace(/\.(tar\.gz|tgz|zip)$/i, ""); }
    catch { return u; }
  };

  // --- Testar escuta ---
  const [engine, setEngine] = useState<"porcupine" | "vosk" | "whisper" | "openwakeword">("porcupine");
  const [word, setWord] = useState("Jarvis");
  const [state, setState] = useState<TestState>("idle");
  const [msg, setMsg] = useState("");
  const [level, setLevel] = useState(0);   // nível do mic 0..1 (medidor de captação)
  const [heard, setHeard] = useState("");  // transcript ao vivo (Vosk/Whisper)
  const [score, setScore] = useState(0);   // score ao vivo (OpenWakeWord)
  const handleRef = useRef<WakeHandle | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const meterRef = useRef<{ stop: () => void } | null>(null);

  // --- Confirmar modelo Vosk (baixa/valida sem mic) ---
  const [voskState, setVoskState] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const [voskMsg, setVoskMsg] = useState("");
  const confirmVosk = async () => {
    setVoskState("loading");
    setVoskMsg(`Baixando o modelo "${voskName(effVoskUrl)}"…`);
    try {
      await loadVoskModel(effVoskUrl);
      setVoskState("ok");
      setVoskMsg(`✓ Modelo "${voskName(effVoskUrl)}" instalado.`);
      try { localStorage.setItem("aiw_wake_vosk", effVoskUrl); } catch { /* noop */ }
      setVoskInstalledUrl(effVoskUrl);
    } catch (e) {
      setVoskState("error");
      setVoskMsg(`Falha ao carregar: ${e instanceof Error ? e.message : String(e)} — verifique a URL/CORS/formato.`);
    }
  };
  const uninstallVosk = () => {
    try { localStorage.removeItem("aiw_wake_vosk"); } catch { /* noop */ }
    setVoskInstalledUrl(null);
    setVoskState("idle");
    setVoskMsg("Desinstalado (o navegador ainda pode manter o arquivo no cache HTTP).");
  };

  // --- Baixar modelo Whisper (on-device; ~150MB na 1ª vez) ---
  const [whState, setWhState] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const [whMsg, setWhMsg] = useState("");
  const downloadWhisper = async () => {
    setWhState("loading");
    setWhMsg("Baixando o Whisper (~150MB na 1ª vez; depois fica em cache)…");
    try {
      await loadWhisperModel();
      setWhState("ok");
      setWhMsg("✓ Whisper instalado (offline a partir de agora).");
      try { localStorage.setItem("aiw_wake_whisper", "1"); } catch { /* noop */ }
      setWhInstalled(true);
    } catch (e) {
      setWhState("error");
      setWhMsg(`Falha ao baixar o Whisper: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
  const uninstallWhisper = async () => {
    await evictTransformers();
    try { localStorage.removeItem("aiw_wake_whisper"); } catch { /* noop */ }
    setWhInstalled(false);
    setWhState("idle");
    setWhMsg("Modelo Whisper removido do cache do navegador.");
  };

  // --- Confirmar modelos OpenWakeWord (baixa/valida os 3 .onnx, sem mic) ---
  const owwMelUrl = (cfg.oww_melspec_url || "").trim() || OWW_MELSPEC_DEFAULT;
  const owwEmbUrl = (cfg.oww_embedding_url || "").trim() || OWW_EMBEDDING_DEFAULT;
  const owwModelUrl = (cfg.oww_model_url || "").trim();
  const [owwState, setOwwState] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const [owwMsg, setOwwMsg] = useState("");
  const confirmOww = async () => {
    if (!owwModelUrl) { setOwwState("error"); setOwwMsg("Informe a URL do seu modelo (.onnx) primeiro."); return; }
    setOwwState("loading");
    setOwwMsg("Baixando/validando os modelos ONNX (melspectrograma + embedding + o seu)…");
    try {
      await loadOwwModels(owwModelUrl, owwMelUrl, owwEmbUrl);
      setOwwState("ok");
      setOwwMsg("✓ Modelos OpenWakeWord carregados. Use o teste abaixo para calibrar a sensibilidade.");
    } catch (e) {
      setOwwState("error");
      setOwwMsg(`Falha ao carregar: ${e instanceof Error ? e.message : String(e)} — verifique as URLs/CORS/formato .onnx.`);
    }
  };

  // Medidor de nível INDEPENDENTE do engine: prova que o mic está captando (mesmo
  // com Porcupine, que não transcreve). Stream próprio, encerrado no stopTest.
  const startMeter = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AC();
      const src = ctx.createMediaStreamSource(stream);
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      let raf = 0;
      let last = 0;
      const tick = () => {
        an.getFloatTimeDomainData(buf);
        let s = 0;
        for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
        const now = performance.now();
        if (now - last > 60) { last = now; setLevel(Math.min(1, Math.sqrt(s / buf.length) * 4)); }
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      meterRef.current = {
        stop: () => {
          cancelAnimationFrame(raf);
          try { src.disconnect(); } catch { /* noop */ }
          try { ctx.close(); } catch { /* noop */ }
          stream.getTracks().forEach((t) => t.stop());
        },
      };
    } catch { /* medidor é opcional */ }
  };

  const stopTest = async () => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    meterRef.current?.stop();
    meterRef.current = null;
    setLevel(0);
    const h = handleRef.current;
    handleRef.current = null;
    if (h) { try { await h.stop(); } catch { /* noop */ } }
  };

  // limpa o mic ao sair do painel
  useEffect(() => () => { void stopTest(); }, []);

  const runTest = async () => {
    if (state === "loading" || state === "listening") { await stopTest(); setState("idle"); setMsg(""); return; }
    setState("loading");
    setMsg(
      engine === "vosk"
        ? `Carregando o modelo "${voskName(effVoskUrl)}"…`
        : engine === "whisper"
        ? "Carregando o Whisper… (baixa ~150MB na 1ª vez)"
        : engine === "openwakeword"
        ? "Carregando os modelos ONNX (baixa na 1ª vez)…"
        : "Preparando… (o navegador vai pedir o microfone)",
    );
    setHeard("");
    setScore(0);
    void startMeter();
    try {
      const handle = await startWakeWord(
        {
          engine,
          callName: word,
          accessKey: cfg.picovoice_key,
          porcupineKeyword: engine === "porcupine" ? word : undefined,
          voskModelUrl: effVoskUrl,
          owwModelUrl,
          owwMelspecUrl: owwMelUrl,
          owwEmbeddingUrl: owwEmbUrl,
          onReady: () => {
            setState("listening");
            setMsg(
              engine === "openwakeword"
                ? "Escutando… diga a palavra do seu modelo (veja o score subir)."
                : engine === "vosk"
                ? `Modelo "${voskName(effVoskUrl)}" pronto. Escutando… diga "${word}".`
                : `Escutando… diga "${word}".`,
            );
          },
          onError: (m) => { setState("error"); setMsg(m); },
          onPartial: (t) => setHeard(t),
          onScore: (s) => setScore(s),
        },
        () => {
          setState("heard");
          setMsg(engine === "openwakeword" ? "✓ Detectado! O modelo disparou acima do limiar." : `✓ Ouvi "${word}"! A wake word está funcionando.`);
          void stopTest();
        },
      );
      handleRef.current = handle;
      // segurança: encerra o teste após 20s se nada acontecer
      timerRef.current = setTimeout(() => {
        void stopTest();
        setState((s) => (s === "heard" ? s : "idle"));
        setMsg((m) => (state === "heard" ? m : "Tempo esgotado — não ouvi a palavra. Tente falar mais perto do mic."));
      }, 20_000);
    } catch (e) {
      setState("error");
      setMsg(e instanceof Error ? e.message : "Falha ao iniciar a escuta.");
    }
  };

  const testing = state === "loading" || state === "listening";

  return (
    <div className="space-y-5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Detecção de voz</h3>

      {/* Porcupine */}
      <div className="space-y-3 rounded-xl border border-border bg-surface2/40 p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-ink">
          <Mic size={15} className="text-muted" /> Porcupine (Picovoice)
        </div>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">AccessKey</span>
          <input
            type="password"
            value={cfg.picovoice_key ?? ""}
            onChange={(e) => patch({ picovoice_key: e.target.value })}
            placeholder="cole sua AccessKey grátis"
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent"
          />
          <span className="mt-1 block text-[11px] text-muted">
            Grátis em <span className="font-mono text-ink-soft">console.picovoice.ai</span>. Necessária para as
            palavras embutidas (Jarvis, Computer…).
          </span>
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">Modelo .ppn custom (opcional)</span>
          <input
            value={cfg.ppn_url ?? ""}
            onChange={(e) => patch({ ppn_url: e.target.value })}
            placeholder="https://…/hey-max.ppn"
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent"
          />
          <span className="mt-1 block text-[11px] text-muted">
            Para uma palavra própria (&quot;hey Max&quot;): gere o .ppn no console da Picovoice e cole a URL. No
            modelo, escolha a palavra &quot;Personalizada&quot;.
          </span>
        </label>
      </div>

      {/* Whisper (on-device, melhor com nomes) */}
      <div className="space-y-3 rounded-xl border border-border bg-surface2/40 p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-ink">
          <Ear size={15} className="text-muted" /> Whisper
        </div>
        <p className="text-[11px] text-muted">
          On-device, sem chave, reconhece <strong>nomes</strong> (&quot;akeno&quot;) muito melhor que o Vosk.
          A palavra vem da <span className="text-ink-soft">Palavra de ativação</span> do modelo. O modelo (~150MB)
          baixa 1x e fica em cache (offline depois).
        </p>
        <div className="flex items-center gap-2">
          {whInstalled ? (
            <button
              onClick={uninstallWhisper}
              className="flex items-center gap-1.5 rounded-lg border border-border bg-bg px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-red-500/10 hover:text-red-400"
            >
              <Trash2 size={13} /> Desinstalar
            </button>
          ) : (
            <button
              onClick={downloadWhisper}
              disabled={whState === "loading"}
              className="flex items-center gap-1.5 rounded-lg border border-border bg-bg px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-hover disabled:opacity-50"
            >
              {whState === "loading" ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
              Baixar modelo
            </button>
          )}
          {whInstalled && <span className="flex items-center gap-1 text-[11px] text-green-400"><Check size={12} /> Instalado</span>}
        </div>
        {whMsg && (
          <p className={`text-[11px] ${whState === "ok" ? "text-green-400" : whState === "error" ? "text-red-400" : "text-muted"}`}>
            {whMsg}
          </p>
        )}
      </div>

      {/* Vosk */}
      <div className="space-y-3 rounded-xl border border-border bg-surface2/40 p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-ink">
          <Ear size={15} className="text-muted" /> Vosk
        </div>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">URL do modelo (.zip ou .tar.gz)</span>
          <input
            value={cfg.vosk_model_url ?? ""}
            onChange={(e) => { patch({ vosk_model_url: e.target.value }); setVoskState("idle"); setVoskMsg(""); }}
            placeholder={VOSK_DEFAULT}
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent"
          />
          <span className="mt-1 block text-[11px]">
            <span className="text-muted">Em uso: </span>
            <span className="font-mono text-ink-soft">{voskName(effVoskUrl)}</span>
            <span className="text-muted"> {usingDefaultVosk ? "(padrão)" : "(seu link)"}</span>
          </span>
        </label>
        <div className="flex items-center gap-2">
          {/* instalado & mesma URL → Desinstalar; mudou a URL → Reinstalar; senão → Baixar */}
          {voskInstalledUrl && voskInstalledUrl === effVoskUrl ? (
            <button
              onClick={uninstallVosk}
              className="flex items-center gap-1.5 rounded-lg border border-border bg-bg px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-red-500/10 hover:text-red-400"
            >
              <Trash2 size={13} /> Desinstalar
            </button>
          ) : (
            <button
              onClick={confirmVosk}
              disabled={voskState === "loading"}
              className="flex items-center gap-1.5 rounded-lg border border-border bg-bg px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-hover disabled:opacity-50"
            >
              {voskState === "loading" ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
              {voskInstalledUrl && voskInstalledUrl !== effVoskUrl ? "Reinstalar (link mudou)" : "Baixar modelo"}
            </button>
          )}
          {voskInstalledUrl === effVoskUrl && <span className="flex items-center gap-1 text-[11px] text-green-400"><Check size={12} /> Instalado</span>}
        </div>
        {voskMsg && (
          <p className={`text-[11px] ${voskState === "ok" ? "text-green-400" : voskState === "error" ? "text-red-400" : "text-muted"}`}>
            {voskMsg}
          </p>
        )}
      </div>

      {/* OpenWakeWord (modelo treinado pelo usuário, ONNX on-device) */}
      <div className="space-y-3 rounded-xl border border-border bg-surface2/40 p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-ink">
          <Ear size={15} className="text-muted" /> OpenWakeWord
        </div>
        <p className="text-[11px] text-muted">
          Palavra/nome <strong>próprio</strong>, com um modelo que você treina (Colab do openWakeWord) e hospeda.
          Roda on-device (ONNX). Precisa das 3 URLs com <span className="text-ink-soft">CORS liberado</span>.
        </p>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">URL do seu modelo (.onnx)</span>
          <input
            value={cfg.oww_model_url ?? ""}
            onChange={(e) => { patch({ oww_model_url: e.target.value }); setOwwState("idle"); setOwwMsg(""); }}
            placeholder="https://…/hey_max.onnx"
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent"
          />
        </label>
        <details className="text-[11px]">
          <summary className="cursor-pointer text-muted hover:text-ink-soft">Modelos compartilhados (avançado)</summary>
          <div className="mt-2 space-y-2">
            <label className="block">
              <span className="mb-1 block font-medium text-muted">Melspectrograma (.onnx)</span>
              <input
                value={cfg.oww_melspec_url ?? ""}
                onChange={(e) => { patch({ oww_melspec_url: e.target.value }); setOwwState("idle"); setOwwMsg(""); }}
                placeholder={OWW_MELSPEC_DEFAULT}
                className="w-full rounded-lg border border-border bg-bg px-3 py-1.5 font-mono text-ink outline-none focus:border-accent"
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-medium text-muted">Embedding (.onnx)</span>
              <input
                value={cfg.oww_embedding_url ?? ""}
                onChange={(e) => { patch({ oww_embedding_url: e.target.value }); setOwwState("idle"); setOwwMsg(""); }}
                placeholder={OWW_EMBEDDING_DEFAULT}
                className="w-full rounded-lg border border-border bg-bg px-3 py-1.5 font-mono text-ink outline-none focus:border-accent"
              />
            </label>
            <p className="text-muted">Vazio = usa os padrões públicos acima.</p>
          </div>
        </details>
        <div className="flex items-center gap-2">
          <button
            onClick={confirmOww}
            disabled={owwState === "loading"}
            className="flex items-center gap-1.5 rounded-lg border border-border bg-bg px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-hover disabled:opacity-50"
          >
            {owwState === "loading" ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
            Confirmar modelos
          </button>
          {owwState === "ok" && <span className="flex items-center gap-1 text-[11px] text-green-400"><Check size={12} /> Carregado</span>}
        </div>
        {owwMsg && (
          <p className={`text-[11px] ${owwState === "ok" ? "text-green-400" : owwState === "error" ? "text-red-400" : "text-muted"}`}>
            {owwMsg}
          </p>
        )}
      </div>

      {/* Testar escuta */}
      <div className="space-y-3 rounded-xl border border-accent/30 bg-accent/5 p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-ink">
          <Ear size={15} className="text-accent" /> Testar escuta
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="block">
            <span className="mb-1 block text-[11px] font-medium text-muted">Engine</span>
            <select
              value={engine}
              onChange={(e) => setEngine(e.target.value as "porcupine" | "vosk" | "whisper" | "openwakeword")}
              disabled={testing}
              className="rounded-lg border border-border bg-bg px-2 py-2 text-sm text-ink outline-none focus:border-accent disabled:opacity-50"
            >
              <option value="porcupine">Porcupine</option>
              <option value="whisper">Whisper</option>
              <option value="vosk">Vosk</option>
              <option value="openwakeword">OpenWakeWord</option>
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-[11px] font-medium text-muted">Palavra</span>
            <input
              value={word}
              onChange={(e) => setWord(e.target.value)}
              disabled={testing}
              placeholder="Jarvis"
              className="w-40 rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent disabled:opacity-50"
            />
          </label>
          <button
            onClick={runTest}
            className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
          >
            {state === "loading" ? <Loader2 size={15} className="animate-spin" /> : state === "heard" ? <Check size={15} /> : <Ear size={15} />}
            {testing ? "Parar" : "Testar"}
          </button>
        </div>
        {msg && (
          <p
            className={`text-xs ${
              state === "heard" ? "text-green-400" : state === "error" ? "text-red-400" : "text-muted"
            }`}
          >
            {state === "listening" && <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-accent align-middle" />}
            {msg}
          </p>
        )}

        {(state === "loading" || state === "listening" || state === "heard") && (
          <div className="space-y-2">
            {/* medidor de captação do mic */}
            <div className="flex items-center gap-2">
              <Mic size={13} className="shrink-0 text-muted" />
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface2">
                <div
                  className="h-full rounded-full bg-accent transition-[width] duration-75"
                  style={{ width: `${Math.round(level * 100)}%` }}
                />
              </div>
            </div>
            {/* OpenWakeWord: barra de score ao vivo (para calibrar o limiar) */}
            {engine === "openwakeword" ? (
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="shrink-0 text-[11px] text-muted">Score</span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface2">
                    <div
                      className={`h-full rounded-full transition-[width] duration-75 ${score >= 0.5 ? "bg-green-400" : "bg-accent"}`}
                      style={{ width: `${Math.round(score * 100)}%` }}
                    />
                  </div>
                  <span className="w-10 shrink-0 text-right font-mono text-[11px] text-ink-soft">{score.toFixed(2)}</span>
                </div>
                <p className="text-[11px] text-muted">Diga sua palavra e veja o pico. Ajuste o limiar no modelo um pouco abaixo do pico.</p>
              </div>
            ) : engine !== "porcupine" ? (
              <p className="text-xs text-ink-soft">
                Entendido: <span className="font-medium text-ink">{heard || "—"}</span>
              </p>
            ) : (
              <p className="text-[11px] text-muted">
                Porcupine não transcreve (é detector de palavra). A barra acima mostra que o mic está captando;
                a palavra só acende quando reconhecida. Sem barra = mic não está chegando.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
