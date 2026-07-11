"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { ArrowLeft, BarChart3, Columns2, FlaskConical, Wrench } from "lucide-react";
import BenchmarkView from "./BenchmarkView";
import CompareView from "./CompareView";
import ToolDebugView from "./ToolDebugView";

type Section = "benchmarks" | "compare" | "tools";

const CARDS: { key: Section; name: string; desc: string; icon: ReactNode }[] = [
  { key: "benchmarks", name: "Benchmarks", desc: "Casos de teste contra 1+ modelos, com nota, custo e histórico", icon: <BarChart3 size={22} /> },
  { key: "compare", name: "Comparações", desc: "Mesmo prompt em vários modelos, lado a lado", icon: <Columns2 size={22} /> },
  { key: "tools", name: "Debug de Tools", desc: "Testar e inspecionar chamadas de ferramentas", icon: <Wrench size={22} /> },
];

function PlayCard({ icon, name, desc, onClick }: { icon: ReactNode; name: string; desc: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="group relative flex h-[168px] flex-col items-center justify-center gap-3 rounded-2xl border border-border bg-surface p-5 text-center transition-all duration-150 hover:-translate-y-0.5 hover:border-accent/40 hover:bg-hover hover:shadow-sm"
    >
      <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
        {icon}
      </span>
      <div className="flex flex-col items-center">
        <p className="text-sm font-semibold text-ink">{name}</p>
        <p className="mt-0.5 line-clamp-2 text-xs leading-4 text-muted">{desc}</p>
      </div>
    </button>
  );
}

/** Casca de uma modalidade: breadcrumb "Playground › <seção>" + conteúdo. */
export function PlaygroundShell({ title, onBack, children }: { title: string; onBack: () => void; children: ReactNode }) {
  return (
    <div className="px-4 py-5 md:px-8 md:py-6">
      <nav className="mb-4 flex items-center gap-1.5 text-sm">
        <button onClick={onBack} className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-muted transition-colors hover:bg-hover hover:text-ink">
          <ArrowLeft size={16} /> Playground
        </button>
        <span className="text-muted">/</span>
        <span className="font-medium text-ink">{title}</span>
      </nav>
      {children}
    </div>
  );
}

export default function PlaygroundView({ onClose }: { onClose: () => void }) {
  const [section, setSection] = useState<Section | null>(null);

  return (
    <div className="h-full flex-1 overflow-y-auto bg-bg">
      {section === null && (
        <div className="mx-auto max-w-5xl px-4 py-6 md:px-8 md:py-8">
          <div className="mb-6 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="flex items-center gap-2 text-2xl font-bold text-ink">
                <FlaskConical size={22} className="text-accent-hover" /> Playground
              </h1>
              <p className="mt-1 text-sm text-muted">Experimente, meça e depure seus modelos e ferramentas.</p>
            </div>
            <button onClick={onClose} className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">
              <ArrowLeft size={16} /> Voltar ao chat
            </button>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {CARDS.map((c) => (
              <PlayCard key={c.key} icon={c.icon} name={c.name} desc={c.desc} onClick={() => setSection(c.key)} />
            ))}
          </div>
        </div>
      )}

      {section === "benchmarks" && (
        <PlaygroundShell title="Benchmarks" onBack={() => setSection(null)}>
          <BenchmarkView />
        </PlaygroundShell>
      )}
      {section === "compare" && (
        <PlaygroundShell title="Comparações" onBack={() => setSection(null)}>
          <CompareView />
        </PlaygroundShell>
      )}
      {section === "tools" && (
        <PlaygroundShell title="Debug de Tools" onBack={() => setSection(null)}>
          <ToolDebugView />
        </PlaygroundShell>
      )}
    </div>
  );
}
