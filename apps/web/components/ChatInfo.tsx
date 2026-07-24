"use client";

import { useEffect, useState } from "react";
import {
  Blocks, Brain, Code2, Coins, FileText, Hash, Info, Loader2, MessageSquare, X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ChatInfo as ChatInfoData } from "@/lib/types";
import { TagInput } from "./ui";

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return `${n}`;
}
function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Painel "Informações" do chat: modelo, mensagens, tokens (entrada/saída), custo,
 *  artefatos e memórias vinculadas. Tags editáveis (salvas via PATCH /chats). */
export default function ChatInfoModal({
  chatId,
  onClose,
  onOpenArtifact,
}: {
  chatId: string;
  onClose: () => void;
  onOpenArtifact?: (identifier: string) => void;
}) {
  const [info, setInfo] = useState<ChatInfoData | null>(null);
  const [tags, setTags] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api.get<ChatInfoData>(`/chats/${chatId}/info`)
      .then((d) => { if (alive) { setInfo(d); setTags(d.tags || []); } })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [chatId]);

  function saveTags(next: string[]) {
    setTags(next);
    api.patch(`/chats/${chatId}`, { tags: next }).catch(() => {});
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Info size={18} className="text-accent-hover" />
          <span className="flex-1 truncate text-sm font-semibold text-ink">{info?.title || "Informações"}</span>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-16 text-muted"><Loader2 size={20} className="animate-spin" /></div>
        ) : !info ? (
          <p className="py-16 text-center text-sm text-muted">Não foi possível carregar.</p>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
            {/* stats */}
            <div className="grid grid-cols-2 gap-2">
              <Stat icon={<MessageSquare size={15} />} label="Mensagens" value={String(info.message_count)} />
              <Stat icon={<Coins size={15} />} label="Custo" value={`$${info.cost.toFixed(4)}`} />
              <Stat icon={<Hash size={15} />} label="Tokens entrada" value={fmtNum(info.tokens_in)} />
              <Stat icon={<Hash size={15} />} label="Tokens saída" value={fmtNum(info.tokens_out)} />
              <Stat icon={<Brain size={15} />} label="Memórias" value={String(info.memory_count)} />
              <Stat icon={<Blocks size={15} />} label="Artefatos" value={String(info.artifacts.length)} />
            </div>

            <div className="rounded-xl border border-border bg-surface2/40 px-3 py-2 text-xs text-muted">
              <p>Modelo: <span className="text-ink-soft">{info.model || "—"}</span></p>
              {info.project_id && (
                <p className="mt-0.5 flex items-center gap-1.5">
                  <Code2 size={12} className="shrink-0 text-accent-hover" />
                  Projeto:{" "}
                  <span className="truncate text-ink-soft">
                    {info.project_name || "(projeto removido)"}
                  </span>
                </p>
              )}
              <p className="mt-0.5">Criado: {fmtDate(info.created_at)} · Atualizado: {fmtDate(info.updated_at)}</p>
            </div>

            {/* tags */}
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted">Etiquetas</p>
              <TagInput tags={tags} onChange={saveTags} placeholder="Ex.: trabalho, ideias…" />
            </div>

            {/* artefatos */}
            {info.artifacts.length > 0 && (
              <div>
                <p className="mb-1.5 text-xs font-medium text-muted">Artefatos nesta conversa</p>
                <ul className="flex flex-col gap-1">
                  {info.artifacts.map((a) => (
                    <li key={a.id}>
                      <button
                        onClick={() => onOpenArtifact?.(a.identifier)}
                        className="flex w-full items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-left transition-colors hover:border-accent/40"
                      >
                        <FileText size={15} className="shrink-0 text-accent-hover" />
                        <span className="min-w-0 flex-1 truncate text-sm text-ink">{a.title}</span>
                        <span className="shrink-0 rounded-full bg-surface2 px-1.5 py-0.5 text-[10px] text-muted">{a.kind} · v{a.version}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2.5 rounded-xl border border-border bg-surface px-3 py-2.5">
      <span className="text-muted">{icon}</span>
      <div className="min-w-0">
        <p className="truncate text-[11px] text-muted">{label}</p>
        <p className="truncate text-sm font-semibold text-ink tabular-nums">{value}</p>
      </div>
    </div>
  );
}
