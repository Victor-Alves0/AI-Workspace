"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Check, KeyRound, Lock, Mail, User as UserIcon, Users } from "lucide-react";
import AuthShell, { AuthButton, AuthField } from "@/components/AuthShell";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";

const PASSOS = 3;

/** Assistente de primeiro uso: existe só enquanto a instalação não tem nenhum usuário.
 *  Cria o administrador e define se outras pessoas podem se cadastrar; depois disso a
 *  configuração inicial (chaves, modelo) continua dentro do app. */
export default function SetupPage() {
  const router = useRouter();
  const [pronto, setPronto] = useState(false);
  const [passo, setPasso] = useState(0);
  const [nome, setNome] = useState("");
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [confirma, setConfirma] = useState("");
  const [aberto, setAberto] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    // já configurada: o assistente não existe mais
    api.get<{ needs_setup?: boolean }>("/auth/config")
      .then((c) => (c.needs_setup ? setPronto(true) : router.replace("/login")))
      .catch(() => setPronto(true));
  }, [router]);

  function continuarConta(e: React.FormEvent) {
    e.preventDefault();
    if (senha !== confirma) { setErro("As senhas não conferem."); return; }
    setErro(null);
    setPasso(2);
  }

  async function concluir() {
    setEnviando(true);
    setErro(null);
    try {
      await api.post<User>("/auth/setup", { name: nome, email, password: senha, allow_signups: aberto });
      try { sessionStorage.setItem("aiw:from-setup", "1"); } catch { /* opcional */ }
      router.replace("/chat");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Falha inesperada";
      if (err instanceof ApiError && err.status === 409) { router.replace("/login"); return; }
      setErro(msg);
      setPasso(1);  // e-mail/senha recusados voltam à etapa deles
    } finally {
      setEnviando(false);
    }
  }

  if (!pronto) return null;

  const voltar = (
    <button type="button" onClick={() => { setErro(null); setPasso(passo - 1); }}
      className="flex items-center gap-1.5 text-xs text-muted transition-colors hover:text-ink">
      <ArrowLeft size={14} /> Voltar
    </button>
  );

  return (
    <AuthShell steps={PASSOS} step={passo} corner={passo > 0 ? voltar : undefined}>
      {passo === 0 && (
        <div className="space-y-7">
          <div className="space-y-2">
            <h1 className="text-xl font-semibold tracking-tight text-ink">Bem-vindo ao AI Workspace</h1>
            <p className="text-sm text-muted">Vamos criar a sua conta de administrador.</p>
          </div>
          <AuthButton type="button" onClick={() => setPasso(1)} autoFocus>Começar</AuthButton>
        </div>
      )}

      {passo === 1 && (
        <form onSubmit={continuarConta} className="space-y-7">
          <h1 className="text-xl font-semibold tracking-tight text-ink">Sua conta</h1>
          <div className="space-y-6">
            <AuthField icon={<UserIcon size={16} />} required autoFocus autoComplete="name" maxLength={80}
              placeholder="Nome" value={nome} onChange={(e) => setNome(e.target.value)} />
            <AuthField icon={<Mail size={16} />} type="email" required autoComplete="email"
              placeholder="E-mail" value={email} onChange={(e) => setEmail(e.target.value)} />
            <AuthField icon={<KeyRound size={16} />} type="password" required minLength={8} autoComplete="new-password"
              placeholder="Senha (mín. 8 caracteres)" value={senha} onChange={(e) => setSenha(e.target.value)} />
            <AuthField icon={<KeyRound size={16} />} type="password" required minLength={8} autoComplete="new-password"
              placeholder="Confirmar senha" value={confirma} onChange={(e) => setConfirma(e.target.value)} />
          </div>
          {erro && <p className="text-sm text-red-400">{erro}</p>}
          <AuthButton type="submit">Continuar</AuthButton>
        </form>
      )}

      {passo === 2 && (
        <div className="space-y-7">
          <h1 className="text-xl font-semibold tracking-tight text-ink">Quem pode entrar?</h1>
          <div className="space-y-2.5">
            {[
              { v: false, icon: <Lock size={16} />, titulo: "Só eu", sub: "Ninguém mais se cadastra. Dá para abrir o cadastro depois, em Administração." },
              { v: true, icon: <Users size={16} />, titulo: "Outras pessoas também", sub: "Qualquer um pode se cadastrar; você aprova cada conta." },
            ].map((o) => (
              <button key={String(o.v)} type="button" onClick={() => setAberto(o.v)}
                className={`flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${aberto === o.v ? "border-accent/60 bg-accent/10" : "border-border hover:bg-hover"}`}>
                <span className={`mt-0.5 ${aberto === o.v ? "text-accent-hover" : "text-muted"}`}>{o.icon}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-ink">{o.titulo}</span>
                  <span className="mt-0.5 block text-xs text-muted">{o.sub}</span>
                </span>
                {aberto === o.v && <Check size={16} className="mt-0.5 text-accent-hover" />}
              </button>
            ))}
          </div>
          {erro && <p className="text-sm text-red-400">{erro}</p>}
          <AuthButton type="button" onClick={concluir} disabled={enviando}>
            {enviando ? "…" : "Concluir"}
          </AuthButton>
        </div>
      )}
    </AuthShell>
  );
}
