"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Lock, MessageSquare } from "lucide-react";
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
  const [needPw, setNeedPw] = useState(false); // link protegido por senha
  const [pw, setPw] = useState("");
  const [pwErr, setPwErr] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (password?: string) => {
    if (!id) return;
    setLoading(true);
    setPwErr(false);
    try {
      const q = password ? `?pw=${encodeURIComponent(password)}` : "";
      const data = await api.get<SharedChat>(`/shared/${id}${q}`);
      setChat(data);
      setNeedPw(false);
      setErr(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setNeedPw(true);
        if (password) setPwErr(true); // tinha senha e errou
      } else if (e instanceof ApiError && e.status === 410) {
        setErr("Este link de compartilhamento expirou.");
      } else if (e instanceof ApiError && e.status === 404) {
        setErr("Esta conversa não existe ou não está mais compartilhada.");
      } else {
        setErr("Não foi possível carregar a conversa.");
      }
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

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
        {needPw ? (
          <div className="mx-auto mt-16 flex max-w-sm flex-col items-center gap-3 rounded-2xl border border-border bg-surface p-6 text-center">
            <Lock size={26} className="text-accent-hover" />
            <p className="text-sm font-medium text-ink">Conversa protegida</p>
            <p className="text-xs text-muted">Digite a senha para visualizar.</p>
            <input
              type="password"
              autoFocus
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && pw.trim()) load(pw.trim()); }}
              placeholder="Senha"
              className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
            />
            {pwErr && <p className="text-xs text-red-400">Senha incorreta.</p>}
            <button
              onClick={() => pw.trim() && load(pw.trim())}
              disabled={!pw.trim() || loading}
              className="w-full rounded-full bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {loading ? "…" : "Ver conversa"}
            </button>
          </div>
        ) : err ? (
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
