"use client";

import { useCallback, useEffect, useState } from "react";
import { Radar, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { InvestigationGraphMeta } from "@/lib/types";
import InvestigationGraphView from "./InvestigationGraphView";

const KIND_LABEL: Record<string, string> = {
  recon: "Recon", re: "Eng. reversa", behavior: "Comportamento", generic: "Geral",
};

/** Painel do Espaço → Investigações: lista os grafos que a IA montou investigando
 *  (recon/RE/comportamento) e mostra o selecionado no canvas. A ESCRITA é sempre da
 *  IA (tool investigation.graph.manage); aqui só olhamos/apagamos. */
export default function InvestigationPanel() {
  const [graphs, setGraphs] = useState<InvestigationGraphMeta[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<{ graphs: InvestigationGraphMeta[] }>("/investigation/graphs")
      .then((r) => setGraphs(r.graphs))
      .catch(() => setGraphs([]));
  }, []);
  useEffect(() => { load(); }, [load]);

  // mantém uma seleção válida quando a lista muda
  useEffect(() => {
    if (!graphs) return;
    if (selected && !graphs.some((g) => g.graph_id === selected)) setSelected(null);
    if (!selected && graphs.length > 0) setSelected(graphs[0].graph_id);
  }, [graphs, selected]);

  async function remove(id: string) {
    if (!confirm("Apagar este grafo de investigação? Não dá para desfazer.")) return;
    await api.del(`/investigation/graphs/${id}`).catch(() => {});
    if (selected === id) setSelected(null);
    load();
  }

  if (graphs === null) {
    return <p className="grid h-40 place-items-center text-sm text-muted">Carregando…</p>;
  }
  if (graphs.length === 0) {
    return (
      <div className="grid h-56 place-items-center px-8 text-center">
        <div className="max-w-md">
          <Radar size={30} className="mx-auto mb-3 text-muted" />
          <p className="text-sm text-ink">Nenhuma investigação ainda.</p>
          <p className="mt-1 text-xs text-muted">
            Num chat, peça à IA para investigar um alvo, um binário ou o comportamento de um
            sistema — ela sonda com as ferramentas e vai montando o grafo aqui, consultável e visual.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-[260px_1fr]">
      {/* lista de grafos */}
      <ul className="flex max-h-[62vh] flex-col gap-1.5 overflow-y-auto pr-1">
        {graphs.map((g) => {
          const active = g.graph_id === selected;
          return (
            <li key={g.graph_id}>
              <button
                onClick={() => setSelected(g.graph_id)}
                className={`group flex w-full items-start gap-2 rounded-xl border px-3 py-2.5 text-left transition-colors ${
                  active ? "border-accent/50 bg-accent/10" : "border-border bg-surface hover:bg-hover"
                }`}
              >
                <Radar size={16} className={`mt-0.5 shrink-0 ${active ? "text-accent" : "text-muted"}`} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-ink">{g.name || "investigação"}</p>
                  {g.target && <p className="truncate font-mono text-[11px] text-muted">{g.target}</p>}
                  <p className="mt-0.5 text-[10px] text-muted">
                    {KIND_LABEL[g.kind] ?? g.kind} · {g.nodes} nós · {g.edges} arestas
                  </p>
                </div>
                <span
                  role="button" tabIndex={0}
                  onClick={(e) => { e.stopPropagation(); remove(g.graph_id); }}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); remove(g.graph_id); } }}
                  title="Apagar"
                  className="shrink-0 rounded p-1 text-muted opacity-0 hover:text-red-500 group-hover:opacity-100"
                >
                  <Trash2 size={13} />
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {/* canvas do grafo selecionado */}
      <div className="min-w-0">
        {selected
          ? <InvestigationGraphView key={selected} graphId={selected} />
          : <p className="grid h-40 place-items-center text-sm text-muted">Selecione uma investigação.</p>}
      </div>
    </div>
  );
}
