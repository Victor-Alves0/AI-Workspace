"use client";

import { useRef, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "@/lib/api";
import { streamResume } from "@/lib/sse";
import type { Chat, ToolEvent } from "@/lib/types";
import { splitStreamArtifacts, type StreamArtifact } from "@/lib/artifacts";

export interface GuardNote {
  name: string;
  action: string;
  fallback_model?: string | null;
}

export interface SubagentChip {
  name: string;
  ctx?: boolean;
  mem?: boolean;
}

/** Estado acumulado de UMA geração (fora do React p/ não re-renderizar por token). */
export interface StreamState {
  acc: string;
  reason: string;
  tools: ToolEvent[];
}

/** Dependências reativas que o subsistema de geração precisa do componente. */
export interface GenerationDeps {
  artifactsEnabled: boolean;
  temporary: boolean;
  setArtifactOpen: (id: string | null) => void;
  setActive: Dispatch<SetStateAction<Chat | null>>;
  reloadMessages: (id: string) => Promise<void>;
  reloadArtifacts: (id: string) => Promise<void>;
  refreshChats: () => void;
}

/**
 * Subsistema de GERAÇÃO do chat: todo o estado "ao vivo" de uma resposta em
 * streaming (texto, raciocínio, ferramentas, imagem, conhecimento, áudio,
 * subagentes, guardas, artefato ao vivo) + o parser de eventos SSE
 * (`makeStreamHandler`), a retomada pós-F5 (`resumeStream`) e o "Parar"
 * (`handleStop`). Extraído do ChatPage para isolar a parte mais entrelaçada.
 *
 * O componente monta o turno (temporário vs. persistente, criar chat, recarregar)
 * e delega a este hook o processamento do stream — via `makeStreamHandler()`, cujo
 * `state` acumulado ele lê ao fim.
 *
 * `getDeps` é lido preguiçosamente (só quando o handler/resume roda, pós-render):
 * assim o hook pode ser chamado no TOPO do componente sem depender da ordem de
 * declaração das dependências (artifactsEnabled/reloadMessages etc. vêm depois).
 */
export function useGeneration(getDeps: () => GenerationDeps) {
  const [streaming, setStreaming] = useState("");
  const [streamingReasoning, setStreamingReasoning] = useState("");
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);
  const [generatingImage, setGeneratingImage] = useState(false);
  const [consultingKnowledge, setConsultingKnowledge] = useState(false);
  const [transcribingAudio, setTranscribingAudio] = useState(false);
  const [subagents, setSubagents] = useState<SubagentChip[]>([]);
  const [guardNote, setGuardNote] = useState<GuardNote | null>(null);
  const [liveArtifact, setLiveArtifact] = useState<StreamArtifact | null>(null);
  const [sending, setSending] = useState(false);
  // "Parar" durante a geração: como interromper o turno atual (cancel no servidor
  // p/ chats persistentes; abort local p/ temporários). null = nada para parar.
  const stopRef = useRef<(() => void) | null>(null);

  function makeStreamHandler() {
    const deps = getDeps();
    const state: StreamState = { acc: "", reason: "", tools: [] };
    // Artefatos ao vivo: blocos <artifact> saem da bolha e vão pro painel.
    // (desativado em chats temporários — o servidor não injeta as instruções lá)
    const artsLive = deps.artifactsEnabled && !deps.temporary;
    // throttle: renderiza no MÁXIMO a cada ~70ms (não por token). Re-parsear o
    // markdown inteiro a cada token travava a UI em respostas longas (O(n²)). Sem
    // timer pendente — o tail final chega pelo reloadMessages ao fim do stream.
    let lastFlush = 0;
    const flush = () => {
      lastFlush = Date.now();
      if (artsLive && state.acc.includes("<artifact")) {
        const { text, live } = splitStreamArtifacts(state.acc);
        setStreaming(text);
        if (live) {
          setLiveArtifact(live);
          deps.setArtifactOpen(live.identifier);
        }
      } else {
        setStreaming(state.acc);
      }
      setStreamingReasoning(state.reason);
    };
    const maybeFlush = () => { if (Date.now() - lastFlush >= 70) flush(); };
    const handler = (ev: any) => {
      if (ev.type === "token") {
        if (state.acc === "") setGeneratingImage(false); // 1º token = respondendo em texto
        state.acc += ev.text;
        maybeFlush();
      } else if (ev.type === "reasoning") {
        state.reason += ev.text;
        maybeFlush();
      } else if (ev.type === "tool_call") {
        const t: ToolEvent = { kind: "call", name: ev.name, data: ev.arguments };
        state.tools.push(t);
        setToolEvents((x) => [...x, t]);
      } else if (ev.type === "tool_result") {
        const t: ToolEvent = { kind: "result", name: ev.name, data: ev.result };
        state.tools.push(t);
        setToolEvents((x) => [...x, t]);
        setGeneratingImage(false); // a imagem (ou o erro) chegou
        setConsultingKnowledge(false); // os trechos/fontes chegaram
      } else if (ev.type === "image_gen") {
        setGeneratingImage(ev.status === "start");
      } else if (ev.type === "knowledge") {
        setConsultingKnowledge(ev.status === "start");
      } else if (ev.type === "audio_router") {
        setTranscribingAudio(ev.status === "start");
      } else if (ev.type === "subagent") {
        // orquestrador delegou a um operário — mostra/atualiza os chips
        if (ev.status === "start") setSubagents((s) => (ev.agent && !s.some((x) => x.name === ev.agent) ? [...s, { name: ev.agent, ctx: ev.ctx, mem: ev.mem }] : s));
        else if (ev.status === "done") setSubagents((s) => s.filter((x) => x.name !== ev.agent));
      } else if (ev.type === "guard") {
        // um Guarda de saída detectou algo e vai refazer a resposta
        setGuardNote({ name: ev.name, action: ev.action, fallback_model: ev.fallback_model });
      } else if (ev.type === "guard_reset") {
        // descarta a tentativa anterior — a resposta boa vem na próxima
        state.acc = ""; state.reason = ""; state.tools = [];
        setToolEvents([]);
        setGeneratingImage(false);
        setConsultingKnowledge(false);
        setTranscribingAudio(false);
        flush();
      } else if (ev.type === "error") {
        state.acc += `\n\n⚠️ Erro: ${ev.message}`;
        setGeneratingImage(false);
        setConsultingKnowledge(false);
        setTranscribingAudio(false);
        flush();
      } else if (ev.type === "artifacts") {
        // resposta persistida criou/atualizou artefatos: abre o último no painel
        const ids: string[] = ev.ids ?? [];
        if (ids.length) deps.setArtifactOpen(ids[ids.length - 1]);
        setLiveArtifact(null);
      } else if (ev.type === "done") {
        // o `done` traz os tool_events DEFINITIVOS: os guardas (que não são streamados
        // como tool_call) e o custo em tokens de cada evento — só conhecido no fim do
        // turno. Substitui os montados durante o stream p/ o badge aparecer na hora,
        // sem esperar um F5.
        const evs = (ev.tool_events ?? []) as ToolEvent[];
        if (evs.length) {
          state.tools = evs;
          setToolEvents(evs);
        }
      } else if (ev.type === "title") {
        // título gerado por IA na 1ª troca: atualiza o cabeçalho na hora
        deps.setActive((a) => (a && ev.title ? { ...a, title: ev.title } : a));
      }
    };
    return { handler, state };
  }

  // Re-assina uma geração ainda em andamento no chat (ex.: o usuário deu F5 no
  // meio de uma resposta). A geração roda em background no servidor; aqui a UI só
  // volta a "ouvir". Só liga o estado "gerando" quando chega o 1º evento real —
  // o servidor manda {type:"idle"} quando não há nada rodando.
  async function resumeStream(id: string) {
    const deps = getDeps();
    let started = false;
    const { handler } = makeStreamHandler();
    try {
      await streamResume(id, (ev) => {
        if (ev.type === "idle") return;
        if (!started) {
          started = true;
          setSending(true);
          setStreaming("");
          setStreamingReasoning("");
          setToolEvents([]);
          // geração retomada (pós-F5) também pode ser parada
          stopRef.current = () => { api.post(`/chats/${id}/stop`).catch(() => {}); };
        }
        handler(ev);
      });
    } catch {
      /* falha ao re-assinar: ignora — as mensagens persistidas já estão na tela */
    }
    if (started) {
      stopRef.current = null;
      setStreaming("");
      setStreamingReasoning("");
      setLiveArtifact(null);
      setSending(false);
      await deps.reloadMessages(id);
      await deps.reloadArtifacts(id);
      deps.refreshChats();
    }
  }

  // botão "Parar" do composer: interrompe o turno em geração (o parcial fica)
  function handleStop() {
    stopRef.current?.();
  }

  return {
    streaming, setStreaming,
    streamingReasoning, setStreamingReasoning,
    toolEvents, setToolEvents,
    generatingImage, setGeneratingImage,
    consultingKnowledge, setConsultingKnowledge,
    transcribingAudio, setTranscribingAudio,
    subagents, setSubagents,
    guardNote, setGuardNote,
    liveArtifact, setLiveArtifact,
    sending, setSending,
    stopRef,
    makeStreamHandler,
    resumeStream,
    handleStop,
  };
}
