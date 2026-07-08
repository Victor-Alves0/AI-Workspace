"use client";

import { useState } from "react";
import { ChevronDown, ExternalLink, Telescope } from "lucide-react";
import type { DeepResearch } from "@/lib/types";
import Markdown from "./Markdown";

/** Card da pesquisa profunda (research.deep.run): fontes numeradas + resumo
 *  destilado (colapsável). O texto principal costuma vir na resposta da IA. */
export default function DeepResearchCard({ data }: { data: DeepResearch }) {
  const [openBrief, setOpenBrief] = useState(false);
  const sources = data.sources ?? [];
  return (
    <div className="my-2 overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3.5 py-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface2 text-accent-hover">
          <Telescope size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-ink">Pesquisa profunda</p>
          <p className="truncate text-xs text-muted">{data.query}</p>
        </div>
        <span className="shrink-0 rounded-full bg-surface2 px-2 py-0.5 text-[10px] text-muted">
          {sources.length} fontes{data.rounds ? ` · ${data.rounds} rodadas` : ""}
        </span>
      </div>

      {data.brief && (
        <div className="border-b border-border">
          <button
            onClick={() => setOpenBrief((v) => !v)}
            className="flex w-full items-center gap-1.5 px-3.5 py-2 text-xs text-muted transition-colors hover:text-ink"
          >
            <ChevronDown size={14} className={`transition-transform ${openBrief ? "" : "-rotate-90"}`} />
            {openBrief ? "Ocultar resumo da pesquisa" : "Ver resumo da pesquisa"}
          </button>
          {openBrief && (
            <div className="px-4 pb-3 pt-0 text-sm">
              <Markdown content={data.brief} />
            </div>
          )}
        </div>
      )}

      <div className="max-h-64 space-y-1 overflow-y-auto p-2">
        {sources.map((s) => (
          <a
            key={s.n} href={s.url} target="_blank" rel="noreferrer"
            className="flex items-start gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-hover"
          >
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded bg-surface2 text-[10px] font-medium text-muted">{s.n}</span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1 truncate text-xs font-medium text-ink">
                <ExternalLink size={11} className="shrink-0 text-muted" /> {s.title || s.url}
              </span>
              <span className="block truncate text-[11px] text-muted">{s.url}</span>
            </span>
          </a>
        ))}
      </div>
    </div>
  );
}
