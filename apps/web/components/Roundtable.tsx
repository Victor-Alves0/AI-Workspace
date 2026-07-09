"use client";

import { useMemo, useRef, useState } from "react";
import { ChevronDown, Pause, Pencil, Play, Plus, Search, StepForward, Trash2, X } from "lucide-react";
import type { Model, ModelConfig, RoundtableConfig, RoundtableParticipant } from "@/lib/types";
import { useClickOutside } from "./ui";

// paleta de cores dos participantes (atribuída por ordem de entrada)
export const RT_COLORS = ["#f59e0b", "#3b82f6", "#10b981", "#ef4444", "#a855f7", "#ec4899", "#14b8a6", "#f97316"];

export function nextColor(used: (string | null | undefined)[]): string {
  return RT_COLORS.find((c) => !used.includes(c)) ?? RT_COLORS[used.length % RT_COLORS.length];
}

/** dropdown compacto p/ escolher um modelo (base ou custom) a adicionar à mesa */
function AddModel({
  models, custom, onAdd,
}: {
  models: Model[];
  custom: ModelConfig[];
  onAdd: (p: { model: string; model_config_id?: string | null; name: string; avatar?: string | null }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useClickOutside<HTMLDivElement>(() => setOpen(false));
  const rows = useMemo(() => {
    const c = custom.map((m) => ({ key: `c:${m.id}`, name: m.name, model: m.base_model, mcid: m.id, avatar: m.avatar_url }));
    const e = models.map((m) => ({ key: `e:${m.id}`, name: m.name, model: m.id, mcid: null as string | null, avatar: null }));
    const all = [...c, ...e];
    const f = q.trim().toLowerCase();
    return f ? all.filter((r) => r.name.toLowerCase().includes(f)) : all;
  }, [models, custom, q]);
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 rounded-full border border-dashed border-border px-3 py-1 text-xs text-muted transition-colors hover:border-accent/50 hover:text-ink"
      >
        <Plus size={13} /> modelo
      </button>
      {open && (
        <div className="absolute left-0 top-9 z-50 w-72 overflow-hidden rounded-xl border border-border bg-surface shadow-menu animate-pop">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <Search size={14} className="text-muted" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar modelo…" className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
          </div>
          <div className="max-h-64 overflow-y-auto p-1">
            {rows.map((r) => (
              <button
                key={r.key}
                onClick={() => { onAdd({ model: r.model, model_config_id: r.mcid, name: r.name, avatar: r.avatar }); setOpen(false); setQ(""); }}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-ink transition-colors hover:bg-hover"
              >
                {r.avatar ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={r.avatar} alt="" className="h-5 w-5 shrink-0 rounded-full object-cover" />
                ) : (
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface2 text-[10px]">{r.name[0]?.toUpperCase()}</span>
                )}
                <span className="truncate">{r.name}</span>
                {r.mcid && <span className="ml-auto shrink-0 text-[10px] text-muted">custom</span>}
              </button>
            ))}
            {rows.length === 0 && <p className="px-3 py-5 text-center text-sm text-muted">Nenhum modelo.</p>}
          </div>
        </div>
      )}
    </div>
  );
}

const selCls = "rounded-lg border border-border bg-surface2 px-2 py-1 text-xs text-ink outline-none transition-colors focus:border-accent";

export default function Roundtable({
  participants, config, running, currentSpeakerId, models, custom,
  onAdd, onRemove, onUpdate, onConfigChange, onRun, onStep, onPause,
}: {
  participants: RoundtableParticipant[];
  config: RoundtableConfig;
  running: boolean;
  currentSpeakerId: string | null;
  models: Model[];
  custom: ModelConfig[];
  onAdd: (p: { model: string; model_config_id?: string | null; name: string; avatar?: string | null }) => void;
  onRemove: (id: string) => void;
  onUpdate: (id: string, patch: Partial<RoundtableParticipant>) => void;
  onConfigChange: (patch: Partial<RoundtableConfig>) => void;
  onRun: () => void;
  onStep: () => void;
  onPause: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const policy = config.turn_policy ?? "round_robin";
  const usedColors = participants.map((p) => p.color);
  const editP = participants.find((p) => p.id === editing) || null;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-2 rounded-2xl border border-border bg-surface/60 px-3 py-2.5">
      {/* participantes */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Mesa</span>
        {participants.map((p) => (
          <span
            key={p.id}
            className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors ${currentSpeakerId === p.id ? "border-transparent" : "border-border bg-surface2"}`}
            style={currentSpeakerId === p.id ? { background: (p.color || "#888") + "22", borderColor: (p.color || "#888") + "66" } : undefined}
          >
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: p.color || "#888" }} />
            <span className="max-w-[120px] truncate text-ink">{p.name}</span>
            {p.persona && <Pencil size={10} className="text-muted" />}
            <button onClick={() => setEditing(editing === p.id ? null : p.id)} title="Editar" className="text-muted transition-colors hover:text-ink"><ChevronDown size={12} /></button>
            <button onClick={() => onRemove(p.id)} title="Remover da mesa" className="text-muted transition-colors hover:text-red-300"><X size={12} /></button>
          </span>
        ))}
        <AddModel models={models} custom={custom} onAdd={onAdd} />
      </div>

      {/* editor de persona do participante selecionado */}
      {editP && (
        <div className="space-y-2 rounded-xl border border-border bg-surface p-2.5">
          <div className="flex items-center gap-2">
            <input
              value={editP.name}
              onChange={(e) => onUpdate(editP.id, { name: e.target.value })}
              placeholder="Nome"
              className="min-w-0 flex-1 rounded-lg border border-border bg-surface2 px-2 py-1 text-sm text-ink outline-none focus:border-accent"
            />
            <div className="flex items-center gap-1">
              {RT_COLORS.map((c) => (
                <button key={c} onClick={() => onUpdate(editP.id, { color: c })} className={`h-4 w-4 rounded-full ${editP.color === c ? "ring-2 ring-offset-1 ring-offset-surface" : ""}`} style={{ background: c }} />
              ))}
            </div>
          </div>
          <textarea
            value={editP.persona || ""}
            onChange={(e) => onUpdate(editP.id, { persona: e.target.value })}
            placeholder="Persona/papel na mesa (ex.: 'Você é o cético e desafia as ideias'). Opcional — soma ao system do modelo."
            rows={2}
            className="w-full resize-y rounded-lg border border-border bg-surface2 px-2.5 py-1.5 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
          />
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-muted">{editP.model}</span>
            <button onClick={() => { onRemove(editP.id); setEditing(null); }} className="flex items-center gap-1 text-xs text-muted transition-colors hover:text-red-300"><Trash2 size={12} /> Remover</button>
          </div>
        </div>
      )}

      {/* controles */}
      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-2">
        <select value={policy} onChange={(e) => onConfigChange({ turn_policy: e.target.value as RoundtableConfig["turn_policy"] })} className={selCls} title="Quem fala em seguida">
          <option value="round_robin">Round-robin</option>
          <option value="manual">Manual</option>
          <option value="moderator">Moderador (LLM)</option>
        </select>

        {policy === "manual" && (
          <select value={config.next ?? ""} onChange={(e) => onConfigChange({ next: e.target.value || null })} className={selCls} title="Próximo a falar">
            <option value="">Próximo: automático</option>
            {participants.map((p) => <option key={p.id} value={p.id}>Próximo: {p.name}</option>)}
          </select>
        )}
        {policy === "moderator" && (
          <select value={config.moderator?.model ?? ""} onChange={(e) => onConfigChange({ moderator: { model: e.target.value } })} className={`${selCls} max-w-[160px]`} title="Modelo moderador">
            <option value="">Moderador: escolha…</option>
            {custom.map((m) => <option key={m.id} value={m.base_model}>{m.name}</option>)}
            {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        )}

        <label className="flex items-center gap-1 text-xs text-muted">
          máx.
          <input
            type="number" min={1} max={20} value={config.max_rounds ?? 6}
            onChange={(e) => onConfigChange({ max_rounds: Math.max(1, Math.min(20, Number(e.target.value) || 6)) })}
            className="w-14 rounded-lg border border-border bg-surface2 px-2 py-1 text-xs text-ink outline-none focus:border-accent"
          />
          rodadas
        </label>

        <div className="ml-auto flex items-center gap-1.5">
          {running ? (
            <button onClick={onPause} className="flex items-center gap-1.5 rounded-full bg-surface2 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-hover">
              <Pause size={13} /> Pausar
            </button>
          ) : (
            <>
              <button onClick={onStep} disabled={participants.length < 1} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-50" title="Um turno">
                <StepForward size={13} /> Passo
              </button>
              <button onClick={onRun} disabled={participants.length < 1} className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50" title="Rodar a conversa">
                <Play size={13} /> Rodar
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
