"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [allowSignups, setAllowSignups] = useState(false);

  useEffect(() => {
    api.get<{ allow_signups: boolean }>("/auth/config").then((c) => setAllowSignups(c.allow_signups)).catch(() => {});
    api
      .get<User>("/auth/me")
      .then((u) => router.replace(u.status === "active" ? "/chat" : "/pending"))
      .catch(() => {});
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const path = mode === "login" ? "/auth/login" : "/auth/register";
      const u = await api.post<User>(path, { email, password });
      router.replace(u.status === "active" ? "/chat" : "/pending");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha inesperada");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex h-screen items-center justify-center overflow-hidden px-4">
      {/* glow sutil da marca ao fundo */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/3 h-[480px] w-[480px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/10 blur-[120px]"
      />

      <form
        onSubmit={submit}
        className="animate-fade-up relative w-full max-w-sm space-y-4 rounded-2xl border border-border bg-surface/80 p-8 shadow-modal backdrop-blur"
      >
        <div className="flex flex-col items-center text-center">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.png" alt="" className="mb-3 h-12 w-12 rounded-xl" />
          <h1 className="text-xl font-semibold tracking-tight text-ink">AI Workspace</h1>
          <p className="mt-1 text-sm text-muted">
            {mode === "login" ? "Entre na sua conta" : "Crie sua conta"}
          </p>
        </div>

        <input
          type="email"
          required
          placeholder="email@exemplo.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-xl border border-border bg-surface2/70 px-3.5 py-2.5 text-sm text-ink outline-none transition-colors placeholder:text-muted focus:border-accent/60"
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder="senha (mín. 8 caracteres)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-xl border border-border bg-surface2/70 px-3.5 py-2.5 text-sm text-ink outline-none transition-colors placeholder:text-muted focus:border-accent/60"
        />

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-xl bg-accent py-2.5 text-sm font-medium text-ink transition-colors hover:bg-accent-hover disabled:opacity-60"
        >
          {loading ? "…" : mode === "login" ? "Entrar" : "Cadastrar"}
        </button>

        {allowSignups && (
          <button
            type="button"
            onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}
            className="w-full text-center text-xs text-muted transition-colors hover:text-ink-soft"
          >
            {mode === "login" ? "Não tem conta? Cadastre-se" : "Já tem conta? Entrar"}
          </button>
        )}
      </form>
    </div>
  );
}
