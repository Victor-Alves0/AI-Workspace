import type { SubagentLive, SubagentTimelineItem } from "./types";

/** Aplica um evento de progresso do subagente (raciocínio, texto, passo ou o
 *  resultado de um passo) à linha do tempo ao vivo, sem mutar a anterior. */
export function applySubagentProgress(live: SubagentLive, ev: Record<string, unknown>): SubagentLive {
  const timeline = [...live.timeline];
  const last = timeline[timeline.length - 1];
  const append = (kind: "reasoning" | "text", text: string) => {
    if (last && last.kind === kind) timeline[timeline.length - 1] = { ...last, text: last.text + text };
    else timeline.push({ kind, text });
  };
  if (typeof ev.reasoning === "string") append("reasoning", ev.reasoning);
  else if (typeof ev.text === "string") append("text", ev.text);
  else if (typeof ev.result === "string") {
    for (let i = timeline.length - 1; i >= 0; i--) {
      const it = timeline[i];
      if (it.kind === "tool" && it.tool === ev.result && it.ok == null) {
        timeline[i] = { ...it, ok: ev.ok !== false };
        break;
      }
    }
  } else if (typeof ev.tool === "string") {
    timeline.push({
      kind: "tool", tool: ev.tool, detail: typeof ev.detail === "string" ? ev.detail : undefined, ok: null,
      args: ev.args && typeof ev.args === "object" ? (ev.args as Record<string, unknown>) : undefined,
    });
  }
  return { ...live, timeline };
}

/** Linha do tempo de um resultado antigo (só com `steps`, sem `timeline`). */
export function timelineFromSteps(steps: { tool: string; detail?: string; ok?: boolean | null }[] = []): SubagentTimelineItem[] {
  return steps.map((s) => ({ kind: "tool" as const, tool: s.tool, detail: s.detail, ok: s.ok ?? null }));
}
