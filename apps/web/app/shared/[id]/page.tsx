"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { MessageSquare } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import Markdown from "@/components/Markdown";

type SharedMessage = { role: string; content: string; created_at: string };
type SharedChat = { title: string; model: string; messages: SharedMessage[]; created_at: string };

/** Página pública (sem login) de uma conversa compartilhada — somente leitura. */
export default function SharedChatPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [chat, setChat] = useState<SharedChat | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.get<SharedChat>(`/shared/${id}`)
      .then(setChat)
      .catch((e) => setErr(e instanceof ApiError && e.status === 404 ? "Esta conversa não existe ou não está mais compartilhada." : "Não foi possível carregar a conversa."));
  }, [id]);

  return (
    <div className="min-h-full bg-bg text-ink">
      <header className="sticky top-0 z-10 border-b border-border bg-bg/80 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center gap-2">
          <MessageSquare size={18} className="text-accent-hover" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{chat?.title ?? "Conversa compartilhada"}</p>
            {chat?.model && <p className="truncate text-xs text-muted">{chat.model}</p>}
          </div>
          <span className="shrink-0 rounded-full bg-surface2 px-2.5 py-0.5 text-[11px] text-muted">Somente leitura</span>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-6">
        {err ? (
          <div className="flex flex-col items-center gap-2 py-20 text-center">
            <MessageSquare size={28} className="text-muted" />
            <p className="text-sm text-muted">{err}</p>
          </div>
        ) : !chat ? (
          <p className="py-20 text-center text-sm text-muted">Carregando…</p>
        ) : chat.messages.length === 0 ? (
          <p className="py-20 text-center text-sm text-muted">Esta conversa ainda não tem mensagens.</p>
        ) : (
          <div className="space-y-5">
            {chat.messages.map((m, i) => (
              <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
                <div className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-[15px] ${m.role === "user" ? "bg-accent/15 text-ink" : "border border-border bg-surface text-ink"}`}>
                  {m.role === "user" ? (
                    <p className="whitespace-pre-wrap">{m.content}</p>
                  ) : (
                    <Markdown content={m.content} />
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        <p className="mt-10 text-center text-xs text-muted">Compartilhado via AI Workspace</p>
      </main>
    </div>
  );
}
