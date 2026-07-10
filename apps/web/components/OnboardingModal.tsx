"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Check, KeyRound, Loader2, Sparkles, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Model, User } from "@/lib/types";
import ModelField from "./ModelField";

/* Wizard de primeiro uso, POR-USUÁRIO: como cada um usa a própria chave, todo
 * novo cadastro passa por aqui (chave do OpenRouter → modelo padrão → pronto).
 * Some ao concluir/pular (marca profile.onboarded). Reabrível pelo Status. */
export default function OnboardingModal({ user, onClose, onDone }: {
  user: User;
  onClose: () => void;
  onDone: () => void;
}) {
  const [step, setStep] = useState(0);
  const [key, setKey] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [keyErr, setKeyErr] = useState<string | null>(null);
  const [keyOk, setKeyOk] = useState(false);
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState(user.default_model ?? "");
  const [finishing, setFinishing] = useState(false);

  // já tem chave? (usuário existente reabrindo) → começa direto no modelo
  useEffect(() => {
    api.get<{ openrouter: boolean }>("/settings/secrets")
      .then((s) => { if (s.openrouter) { setKeyOk(true); setStep(1); } })
      .catch(() => {});
  }, []);

  // carrega modelos quando a chave está ok (passo do modelo)
  useEffect(() => {
    if (keyOk) api.get<Model[]>("/settings/models").then(setModels).catch(() => {});
  }, [keyOk]);

  async function saveKey() {
    setSavingKey(true); setKeyErr(null);
    try {
      await api.put("/settings/secrets/openrouter", { api_key: key.trim() });
      await api.get("/settings/models"); // valida a chave (lista modelos)
      setKeyOk(true);
      setStep(1);
    } catch (e) {
      setKeyErr(e instanceof ApiError ? e.message : "Falha ao salvar/validar a chave");
    } finally {
      setSavingKey(false);
    }
  }

  async function finish(skip = false) {
    setFinishing(true);
    try {
      if (!skip && model) await api.put("/settings/default-model", { model }).catch(() => {});
      await api.put("/settings/profile", { onboarded: true });
    } finally {
      setFinishing(false);
      onDone();
      onClose();
    }
  }

  const steps = ["Boas-vindas", "Chave de API", "Modelo", "Pronto"];
  // passo visível: 0 boas-vindas, 1 chave, 2 modelo, 3 pronto — mas se a chave já
  // existe pulamos direto pro modelo. Normalizo o índice de exibição.
  const view = step === 0 ? "welcome" : !keyOk ? "key" : step >= 3 ? "done" : "model";

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm">
      <div className="animate-pop flex w-full max-w-md flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-modal">
        <div className="flex items-center justify-between px-5 py-3">
          <div className="flex items-center gap-2 text-sm font-medium text-ink">
            <Sparkles size={16} className="text-accent-hover" /> Configuração inicial
          </div>
          <button onClick={() => finish(true)} title="Pular" className="rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-ink">
            <X size={18} />
          </button>
        </div>

        {/* progresso */}
        <div className="flex gap-1 px-5 pb-3">
          {steps.map((_, i) => {
            const active = view === "welcome" ? i === 0 : view === "key" ? i <= 1 : view === "model" ? i <= 2 : i <= 3;
            return <div key={i} className={`h-1 flex-1 rounded-full ${active ? "bg-accent" : "bg-surface2"}`} />;
          })}
        </div>

        <div className="px-5 pb-5">
          {view === "welcome" && (
            <div className="space-y-3 py-2">
              <h2 className="text-xl font-semibold text-ink">Bem-vindo ao AI Workspace 👋</h2>
              <p className="text-sm leading-6 text-muted">
                É o <span className="text-ink-soft">seu</span> workspace de IA: você usa a sua própria chave de API e todas as
                configurações ficam guardadas só na sua conta. Vamos deixar tudo pronto em menos de um minuto.
              </p>
              <button onClick={() => setStep(keyOk ? 2 : 1)} className="mt-2 flex w-full items-center justify-center gap-2 rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
                Começar <ArrowRight size={16} />
              </button>
            </div>
          )}

          {view === "key" && (
            <div className="space-y-3 py-2">
              <div className="flex items-center gap-2 text-ink"><KeyRound size={18} className="text-accent-hover" /><h2 className="text-lg font-semibold">Sua chave do OpenRouter</h2></div>
              <p className="text-sm leading-6 text-muted">
                O OpenRouter dá acesso a centenas de modelos com uma única chave. Crie a sua em{" "}
                <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer" className="text-accent-hover underline">openrouter.ai/keys</a>{" "}
                e cole abaixo — ela fica <span className="text-ink-soft">cifrada</span> e é só sua.
              </p>
              <input
                type="password" autoFocus value={key}
                onChange={(e) => setKey(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && key.trim()) saveKey(); }}
                placeholder="sk-or-v1-…"
                className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-sm text-ink outline-none focus:border-accent"
              />
              {keyErr && <p className="text-xs text-red-400">{keyErr}</p>}
              <div className="flex items-center justify-between gap-3">
                <button onClick={() => finish(true)} className="text-xs text-muted transition-colors hover:text-ink">Configurar depois</button>
                <button onClick={saveKey} disabled={!key.trim() || savingKey} className="flex items-center gap-2 rounded-full bg-accent px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
                  {savingKey ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} Salvar e continuar
                </button>
              </div>
            </div>
          )}

          {view === "model" && (
            <div className="space-y-3 py-2">
              <h2 className="text-lg font-semibold text-ink">Escolha um modelo padrão</h2>
              <p className="text-sm leading-6 text-muted">Ele vem selecionado ao abrir um chat novo — dá para trocar quando quiser.</p>
              <ModelField models={models} value={model} onChange={setModel} placeholder="Buscar um modelo…" />
              <div className="flex items-center justify-between gap-3 pt-1">
                <button onClick={() => setStep(3)} className="text-xs text-muted transition-colors hover:text-ink">Pular</button>
                <button onClick={() => setStep(3)} disabled={!model} className="flex items-center gap-2 rounded-full bg-accent px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
                  Continuar <ArrowRight size={16} />
                </button>
              </div>
            </div>
          )}

          {view === "done" && (
            <div className="space-y-3 py-2 text-center">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-green-500/15 text-green-400"><Check size={24} /></div>
              <h2 className="text-lg font-semibold text-ink">Tudo pronto!</h2>
              <p className="text-sm leading-6 text-muted">Sua conta está configurada. Faça uma pergunta e comece a explorar.</p>
              <button onClick={() => finish(false)} disabled={finishing} className="mt-1 flex w-full items-center justify-center gap-2 rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
                {finishing ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={16} />} Ir para o chat
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
