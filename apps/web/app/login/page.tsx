"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, Mail, ShieldCheck } from "lucide-react";
import AuthShell, { AuthButton, AuthField } from "@/components/AuthShell";
import { api, ApiError } from "@/lib/api";
import { measure } from "@/lib/trace";
import type { User } from "@/lib/types";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [need2fa, setNeed2fa] = useState(false);
  const [totp, setTotp] = useState("");
  const [allowSignups, setAllowSignups] = useState(false);

  useEffect(() => {
    api.get<{ allow_signups: boolean; needs_setup?: boolean }>("/auth/config")
      .then((c) => {
        // instalação sem usuários: o assistente cria o administrador
        if (c.needs_setup) router.replace("/setup");
        else setAllowSignups(c.allow_signups);
      })
      .catch(() => {});
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
      const payload: Record<string, string> = { email, password };
      if (mode === "login" && need2fa) payload.totp_code = totp;
      // rastreia o clique no login (do clique ao redirect) — o ponto de partida
      // do rastro fim-a-fim que o painel de Observabilidade correlaciona
      const u = await measure(`${mode}-click`, () => api.post<User>(path, payload));
      router.replace(u.status === "active" ? "/chat" : "/pending");
    } catch (err) {
      // 2FA: o servidor responde 401 com detail "2fa_required" (pedir código) ou
      // "2fa_invalid" (código errado) — o front troca para a etapa do código.
      const detail = err instanceof ApiError ? err.message : "";
      if (detail === "2fa_required") {
        setNeed2fa(true); setError(null);
      } else if (detail === "2fa_invalid") {
        setNeed2fa(true); setError("Código de verificação inválido.");
      } else {
        setError(detail || "Falha inesperada");
      }
    } finally {
      setLoading(false);
    }
  }

  const alternar = () => { setMode(mode === "login" ? "register" : "login"); setError(null); setNeed2fa(false); };

  return (
    <AuthShell
      corner={allowSignups && (
        <div className="flex items-center gap-3">
          <span className="hidden text-xs text-muted sm:inline">{mode === "login" ? "Ainda não tem conta?" : "Já tem conta?"}</span>
          <button
            type="button"
            onClick={alternar}
            className="rounded-lg bg-white px-3.5 py-1.5 text-xs font-medium text-black transition-opacity hover:opacity-90"
          >
            {mode === "login" ? "Criar conta" : "Entrar"}
          </button>
        </div>
      )}
    >
      <form onSubmit={submit} className="space-y-7">
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          {mode === "login" ? "Entre no seu AI Workspace" : "Crie sua conta"}
        </h1>

        <div className="space-y-6">
          <AuthField icon={<Mail size={16} />} type="email" required autoFocus autoComplete="email"
            placeholder="E-mail" value={email} onChange={(e) => setEmail(e.target.value)} />
          <AuthField icon={<KeyRound size={16} />} type="password" required minLength={8}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            placeholder={mode === "login" ? "Senha" : "Senha (mín. 8 caracteres)"}
            value={password} onChange={(e) => setPassword(e.target.value)} />
          {mode === "login" && need2fa && (
            <AuthField icon={<ShieldCheck size={16} />} inputMode="numeric" autoFocus required
              placeholder="Código do app autenticador" value={totp}
              onChange={(e) => setTotp(e.target.value.replace(/\D/g, "").slice(0, 6))}
              className="[&_input]:font-mono [&_input]:tracking-widest" />
          )}
        </div>

        {mode === "register" && (
          <p className="text-xs text-muted">Sua conta fica pendente até um administrador aprovar.</p>
        )}
        {error && <p className="text-sm text-red-400">{error}</p>}

        <AuthButton type="submit" disabled={loading}>
          {loading ? "…" : mode === "login" ? "Entrar" : "Criar conta"}
        </AuthButton>
      </form>
    </AuthShell>
  );
}
