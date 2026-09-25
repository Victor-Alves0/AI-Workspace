"use client";

import { forwardRef, useState } from "react";
import { Eye, EyeOff } from "lucide-react";

/** Moldura das telas de entrada (login e assistente de primeiro uso): um cartão em duas
 *  metades — a arte da marca à esquerda, o formulário à direita. No celular a arte sai
 *  e o formulário ocupa a tela. `steps`/`step` desenham os traços de progresso sobre a
 *  arte (assistente); `corner` é o canto superior direito do formulário. */
export default function AuthShell({
  children, corner, steps = 0, step = 0,
}: {
  children: React.ReactNode;
  corner?: React.ReactNode;
  steps?: number;
  step?: number;
}) {
  return (
    <div className="flex min-h-full items-center justify-center bg-bg p-0 sm:p-6">
      <div className="animate-fade-up relative grid min-h-[100dvh] w-full max-w-5xl overflow-hidden border-border bg-[#0b0b0d] sm:min-h-[600px] sm:rounded-2xl sm:border sm:shadow-modal md:grid-cols-[1fr_1.05fr]">
        {/* celular: a mesma arte vira o FUNDO da tela, com blur e um véu escuro opaco
            por cima para o formulário continuar legível (no desktop ela fica na coluna) */}
        <div aria-hidden className="pointer-events-none absolute inset-0 md:hidden">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/login-hero.webp" alt="" className="h-full w-full scale-110 object-cover opacity-80 blur-xl" />
          <div className="absolute inset-0 bg-gradient-to-b from-[#0b0b0d]/55 via-[#0b0b0d]/70 to-[#0b0b0d]/90" />
        </div>
        <div className="relative hidden md:block">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/login-hero.webp" alt="" className="absolute inset-0 h-full w-full object-cover" />
          <div aria-hidden className="absolute inset-0 bg-gradient-to-r from-transparent via-transparent to-[#0b0b0d]/70" />
          <div className="absolute left-8 top-8 flex items-center gap-2.5">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/logo.png" alt="" className="h-7 w-7 rounded-lg" />
            <span className="text-[15px] font-semibold tracking-tight text-white">AI Workspace</span>
          </div>
          {steps > 1 && (
            <div className="absolute bottom-9 left-1/2 flex -translate-x-1/2 gap-2.5">
              {Array.from({ length: steps }, (_, i) => (
                <span key={i} className={`h-[3px] w-9 rounded-full transition-colors duration-300 ${i <= step ? "bg-white" : "bg-white/20"}`} />
              ))}
            </div>
          )}
        </div>

        <div className="relative z-10 flex flex-col px-7 py-8 sm:px-12">
          <div className="flex min-h-[36px] items-center justify-between gap-3">
            <div className="flex items-center gap-2.5 md:invisible">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/logo.png" alt="" className="h-7 w-7 rounded-lg" />
              <span className="text-[15px] font-semibold tracking-tight text-ink">AI Workspace</span>
            </div>
            {corner}
          </div>
          <div className="flex flex-1 items-center justify-center py-8">
            <div className="w-full max-w-[320px]">{children}</div>
          </div>
          <p className="text-center text-[11px] text-muted">
            <a href="/privacy" className="transition-colors hover:text-ink-soft">Privacidade</a>
            {" · "}
            <a href="/terms" className="transition-colors hover:text-ink-soft">Termos</a>
          </p>
        </div>
      </div>
    </div>
  );
}

/** Campo sublinhado com ícone (padrão das telas de entrada). */
export const AuthField = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement> & {
  icon: React.ReactNode;
}>(function AuthField({ icon, type, className = "", ...props }, ref) {
  const [ver, setVer] = useState(false);
  const senha = type === "password";
  return (
    <label className={`group flex items-center gap-3 border-b border-border pb-2 transition-colors focus-within:border-accent ${className}`}>
      <span className="text-muted transition-colors group-focus-within:text-accent-hover">{icon}</span>
      <input
        ref={ref}
        type={senha && ver ? "text" : type}
        className="min-w-0 flex-1 bg-transparent py-1 text-sm text-ink outline-none placeholder:text-muted"
        {...props}
      />
      {senha && (
        <button type="button" tabIndex={-1} onClick={() => setVer((v) => !v)} title={ver ? "Ocultar senha" : "Mostrar senha"} className="text-muted transition-colors hover:text-ink">
          {ver ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
      )}
    </label>
  );
});

/** Botão principal das telas de entrada. */
export function AuthButton({ children, className = "", ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`w-full rounded-lg bg-gradient-to-b from-accent-hover to-accent py-2.5 text-sm font-medium text-white shadow-[0_8px_24px_-8px_rgb(var(--c-accent)/0.6)] transition-[filter,opacity] hover:brightness-110 disabled:opacity-60 ${className}`}
    >
      {children}
    </button>
  );
}
