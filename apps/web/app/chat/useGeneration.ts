"use client";

import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
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
  // true se `id` ainda é o chat que o usuário está vendo (checado ao vivo). Os
  // handlers de stream só pintam o estado global quando o dono deles está ativo.
  isActiveChat: (id: string | null) => boolean;
  // o provider recusou o nível de raciocínio pedido e o backend rebaixou; reflete
  // no seletor (ex.: "xhigh" pedido, "high" aceito).
  onReasoningEffort?: (effort: string) => void;
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
  // Só pode existir uma assinatura de retomada por página. Abrir o mesmo chat
  // duas vezes rapidamente antes fazia dois leitores processarem os mesmos eventos
  // (tools duplicadas e um leitor antigo limpando o estado do mais novo).
  const resumeAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => resumeAbortRef.current?.abort(), []);

  function makeStreamHandler(getOwnerId?: () => string | null) {
    const deps = getDeps();
    const state: StreamState = { acc: "", reason: "", tools: [] };
    // dono deste stream ainda é o chat ativo? (checado ao vivo a cada evento).
    // Enquanto for, pinta o estado global; se o usuário trocou de chat, o handler
    // segue ACUMULANDO em `state` (p/ notify/persistência) mas NÃO toca a UI —
    // assim o parcial de um chat nunca aparece/apaga no outro.
    const paint = () => deps.isActiveChat(getOwnerId ? getOwnerId() : null);
    // Artefatos ao vivo: blocos <artifact> saem da bolha e vão pro painel.
    // (desativado em chats temporários — o servidor não injeta as instruções lá)
    const artsLive = deps.artifactsEnabled && !deps.temporary;
    // throttle: renderiza no MÁXIMO a cada ~70ms (não por token). Re-parsear o
    // markdown inteiro a cada token travava a UI em respostas longas (O(n²)). O
    // timer de trailing flush é importante: reasoning/texto costuma parar logo antes
    // de uma tool demorada; sem ele, o último trecho ficava invisível até a próxima
    // etapa e dava a impressão de que o stream havia travado.
    let lastFlush = 0;
    let flushTimer: ReturnType<typeof setTimeout> | null = null;
    // O backend descarta o texto provisório emitido antes de uma tool e começa uma
    // nova resposta na iteração seguinte. Mantemos o provisório visível enquanto a
    // tool roda, mas o substituímos assim que chega o primeiro token pós-tool.
    let resetTextOnNextToken = false;
    // Auto-abre o painel UMA vez por artefato (identifier). Sem isto, cada flush
    // reabria o painel — se o usuário fechasse durante a geração, o próximo flush
    // (~70ms) reabria. Guardamos o id já aberto; só reabrimos p/ um artefato NOVO.
    let autoOpenedId: string | null = null;
    const flush = () => {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      lastFlush = Date.now();
      if (!paint()) return;
      if (artsLive && state.acc.includes("<artifact")) {
        const { text, live } = splitStreamArtifacts(state.acc);
        setStreaming(text);
        if (live) {
          setLiveArtifact(live);
          if (live.identifier !== autoOpenedId) {
            autoOpenedId = live.identifier;
            deps.setArtifactOpen(live.identifier);
          }
        }
      } else {
        setStreaming(state.acc);
      }
      setStreamingReasoning(state.reason);
    };
    const maybeFlush = () => {
      const wait = 70 - (Date.now() - lastFlush);
      if (wait <= 0) {
        flush();
      } else if (flushTimer === null) {
        flushTimer = setTimeout(flush, wait);
      }
    };
    const handler = (ev: any) => {
      if (ev.type === "token") {
        if (resetTextOnNextToken) {
          state.acc = "";
          resetTextOnNextToken = false;
        }
        if (state.acc === "" && paint()) setGeneratingImage(false); // 1º token = respondendo em texto
        state.acc += ev.text;
        maybeFlush();
      } else if (ev.type === "reasoning") {
        state.reason += ev.text;
        maybeFlush();
      } else if (ev.type === "reasoning_effort") {
        // provider recusou o nível pedido; o backend rebaixou → o seletor reflete
        if (paint()) deps.onReasoningEffort?.(ev.effort);
      } else if (ev.type === "tool_call") {
        // pinta imediatamente o último trecho de texto/raciocínio antes de trocar
        // para a fase de ferramenta (que pode levar vários segundos).
        flush();
        resetTextOnNextToken = true;
        const t: ToolEvent = { kind: "call", name: ev.name, data: ev.arguments };
        state.tools.push(t);
        if (paint()) setToolEvents((x) => [...x, t]);
      } else if (ev.type === "tool_result") {
        flush();
        const t: ToolEvent = { kind: "result", name: ev.name, data: ev.result };
        state.tools.push(t);
        if (paint()) {
          setToolEvents((x) => [...x, t]);
          setGeneratingImage(false); // a imagem (ou o erro) chegou
          setConsultingKnowledge(false); // os trechos/fontes chegaram
        }
      } else if (ev.type === "image_gen") {
        if (paint()) setGeneratingImage(ev.status === "start");
      } else if (ev.type === "knowledge") {
        if (paint()) setConsultingKnowledge(ev.status === "start");
      } else if (ev.type === "audio_router") {
        if (paint()) setTranscribingAudio(ev.status === "start");
      } else if (ev.type === "subagent") {
        // orquestrador delegou a um operário — mostra/atualiza os chips
        if (!paint()) return;
        if (ev.status === "start") setSubagents((s) => (ev.agent && !s.some((x) => x.name === ev.agent) ? [...s, { name: ev.agent, ctx: ev.ctx, mem: ev.mem }] : s));
        else if (ev.status === "done") setSubagents((s) => s.filter((x) => x.name !== ev.agent));
      } else if (ev.type === "guard") {
        // um Guarda de saída detectou algo e vai refazer a resposta
        if (paint()) setGuardNote({ name: ev.name, action: ev.action, fallback_model: ev.fallback_model });
      } else if (ev.type === "guard_reset") {
        // descarta a tentativa anterior — a resposta boa vem na próxima
        state.acc = ""; state.reason = ""; state.tools = [];
        resetTextOnNextToken = false;
        if (paint()) {
          setToolEvents([]);
          setGeneratingImage(false);
          setConsultingKnowledge(false);
          setTranscribingAudio(false);
        }
        flush();
      } else if (ev.type === "error") {
        state.acc += `\n\n⚠️ Erro: ${ev.message}`;
        if (paint()) {
          setGeneratingImage(false);
          setConsultingKnowledge(false);
          setTranscribingAudio(false);
        }
        flush();
      } else if (ev.type === "stopped") {
        // garante que o parcial que ainda estava no throttle apareça antes de o
        // chamador recarregar a mensagem persistida.
        flush();
      } else if (ev.type === "artifacts") {
        // resposta persistida criou/atualizou artefatos: abre o último no painel
        if (!paint()) return;
        const ids: string[] = ev.ids ?? [];
        if (ids.length) deps.setArtifactOpen(ids[ids.length - 1]);
        setLiveArtifact(null);
      } else if (ev.type === "done") {
        // `content` é autoritativo. Entre iterações de tools o backend zera o texto
        // provisório ("vou pesquisar...") e redige a resposta final; concatenar todos
        // os tokens no cliente deixava a UI presa/exibindo a etapa antiga até o SSE
        // fechar. Aplicamos o snapshot final assim que ele chega.
        if (typeof ev.content === "string") state.acc = ev.content;
        if (typeof ev.reasoning?.text === "string") state.reason = ev.reasoning.text;
        // o `done` traz os tool_events DEFINITIVOS: os guardas (que não são streamados
        // como tool_call) e o custo em tokens de cada evento — só conhecido no fim do
        // turno. Substitui os montados durante o stream p/ o badge aparecer na hora,
        // sem esperar um F5.
        if (Array.isArray(ev.tool_events)) {
          const evs = ev.tool_events as ToolEvent[];
          state.tools = evs;
          if (paint()) setToolEvents(evs);
        }
        flush();
      } else if (ev.type === "title") {
        // título gerado por IA na 1ª troca: atualiza o cabeçalho na hora
        if (paint()) deps.setActive((a) => (a && ev.title ? { ...a, title: ev.title } : a));
      }
    };
    const dispose = () => {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
    };
    return { handler, state, dispose };
  }

  // Re-assina uma geração ainda em andamento no chat (ex.: o usuário deu F5 no
  // meio de uma resposta). A geração roda em background no servidor; aqui a UI só
  // volta a "ouvir". Só liga o estado "gerando" quando chega o 1º evento real —
  // o servidor manda {type:"idle"} quando não há nada rodando.
  async function resumeStream(id: string) {
    const deps = getDeps();
    let started = false;
    const { handler, dispose } = makeStreamHandler(() => id);
    resumeAbortRef.current?.abort();
    const controller = new AbortController();
    resumeAbortRef.current = controller;
    try {
      await streamResume(id, (ev) => {
        if (ev.type === "idle") return;
        if (!started) {
          started = true;
          // só liga o estado "gerando" se este chat ainda é o que o usuário vê
          // (ele pode ter trocado de chat antes do 1º evento chegar).
          if (deps.isActiveChat(id)) {
            setSending(true);
            setStreaming("");
            setStreamingReasoning("");
            setToolEvents([]);
            // geração retomada (pós-F5) também pode ser parada
            stopRef.current = () => { api.post(`/chats/${id}/stop`).catch(() => {}); };
          }
        }
        handler(ev);
      }, controller.signal);
    } catch {
      /* falha ao re-assinar: ignora — as mensagens persistidas já estão na tela */
    }
    dispose();
    // Uma retomada mais nova já assumiu o estado: a antiga não pode desligar o
    // composer nem recarregar mensagens por cima dela ao terminar o abort.
    if (resumeAbortRef.current !== controller) return;
    resumeAbortRef.current = null;
    // ao encerrar, só mexe na UI/recarrega se o chat ainda está aberto — senão
    // sobrescreveria a tela do chat para onde o usuário navegou.
    if (started && deps.isActiveChat(id)) {
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
