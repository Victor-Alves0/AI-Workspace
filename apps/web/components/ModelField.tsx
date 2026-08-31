"use client";

import { useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Cpu, Link2, Search } from "lucide-react";
import type { Model, ModelConfig } from "@/lib/types";
import { AnchoredMenu, dismissKeyboard, finePointer } from "./ui";

interface Row {
  key: string; // id externo, ou "custom:<id>"
  name: string;
  avatar?: string | null;
  external: boolean;
  modelId: string;
  local?: boolean;
  provider?: string;
  custom?: ModelConfig;
}

/** Campo de seleção de modelo com a MESMA aparência do seletor do bate-papo
 *  (busca + abas + lista), mas como campo de formulário. Por padrão só lista
 *  modelos externos (OpenRouter); `includeCustom` adiciona os modelos do usuário. */
export default function ModelField({
  models,
  custom = [],
  value,
  onChange,
  placeholder = "Selecionar modelo",
  includeCustom = false,
  className = "",
}: {
  models: Model[];
  custom?: ModelConfig[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  includeCustom?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"all" | "providers" | "custom" | "local">("all");
  const [q, setQ] = useState("");
  const btnRef = useRef<HTMLButtonElement>(null);

  const rows: Row[] = useMemo(() => {
    const customRows: Row[] = includeCustom
      ? custom.map((c) => ({ key: `custom:${c.id}`, name: c.name, avatar: c.avatar_url, external: false, modelId: c.base_model, custom: c }))
      : [];
    const extRows: Row[] = models.filter((m) => !m.local).map((m) => ({ key: m.id, name: m.name, external: true, modelId: m.id, provider: m.provider }))
      .sort((a, b) => (a.provider === "OpenRouter" ? 1 : 0) - (b.provider === "OpenRouter" ? 1 : 0));
    const localRows: Row[] = models.filter((m) => m.local).map((m) => ({ key: m.id, name: m.name, external: true, local: true, modelId: m.id, provider: m.provider }));
    const all = [...customRows, ...extRows, ...localRows];
    const base =
      tab === "providers" ? extRows
      : tab === "custom" ? customRows
      : tab === "local" ? localRows
      : all;
    const f = q.trim().toLowerCase();
    return f ? base.filter((r) => r.name.toLowerCase().includes(f) || (r.provider ?? "").toLowerCase().includes(f)) : base;
  }, [custom, models, tab, q, includeCustom]);

  const label = useMemo(() => {
    if (!value) return "";
    if (value.startsWith("custom:")) return custom.find((c) => c.id === value.slice(7))?.name ?? value;
    return models.find((m) => m.id === value)?.name ?? value;
  }, [value, models, custom]);

  const tabs: [typeof tab, string][] = includeCustom
    ? [["all", "Tudo"], ["providers", "Providers"], ["custom", "Custom"], ["local", "Local"]]
    : [["all", "Tudo"], ["providers", "Providers"], ["local", "Local"]];

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={className || "flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none transition-colors hover:border-accent/50"}
      >
        <span title={label || placeholder} className={`truncate ${label ? "text-ink" : "text-muted"}`}>{label || placeholder}</span>
        <ChevronDown size={16} className="shrink-0 text-muted" />
      </button>

      {open && (
        <AnchoredMenu
          anchorRef={btnRef}
          onClose={() => setOpen(false)}
          align="left"
          className="w-[360px] max-w-[calc(100vw-1.5rem)] overflow-hidden !rounded-2xl !p-0"
        >
          <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
            <Search size={16} className="text-muted" />
            <input
              autoFocus={finePointer()}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Pesquisar um modelo"
              className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
            />
          </div>
          <div className="flex items-center gap-1 border-b border-border px-2 py-1.5 text-sm">
            {tabs.map(([key, lbl]) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                className={`rounded-lg px-3 py-1 transition-colors ${
                  tab === key ? "bg-surface2 font-medium text-ink" : "text-muted hover:text-ink-soft"
                }`}
              >
                {lbl}
              </button>
            ))}
          </div>
          {/* dvh + dismissKeyboard: ver ModelPicker — scroll de lista com teclado
              aberto trava no iOS */}
          <div onTouchMove={dismissKeyboard} className="max-h-[min(18rem,55dvh)] overflow-y-auto overscroll-contain p-1.5">
            {rows.map((r) => (
              <button
                key={r.key}
                type="button"
                onClick={() => { onChange(r.key); setOpen(false); }}
                className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left transition-colors hover:bg-hover"
              >
                {r.avatar ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={r.avatar} alt="" className="h-6 w-6 shrink-0 rounded-full object-cover" />
                ) : (
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface2 text-[10px] text-ink">
                    {r.name[0]?.toUpperCase()}
                  </span>
                )}
                <span title={r.name} className="flex-1 truncate text-sm text-ink">{r.name}</span>
                {r.provider && (
                  <span className="shrink-0 rounded bg-surface2 px-1.5 py-0.5 text-[10px] text-muted">{r.provider}</span>
                )}
                {r.local ? (
                  <Cpu size={13} className="shrink-0 text-accent-hover" />
                ) : r.external ? (
                  <Link2 size={13} className="shrink-0 text-muted" />
                ) : null}
                {value === r.key && <Check size={16} className="shrink-0 text-accent" />}
              </button>
            ))}
            {rows.length === 0 && (
              <p className="px-3 py-6 text-center text-sm text-muted">
                {tab === "local" ? "Nenhum modelo local. Configure o Ollama em Conexões." : "Nenhum modelo."}
              </p>
            )}
          </div>
        </AnchoredMenu>
      )}
    </>
  );
}
