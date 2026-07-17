"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

export interface ConfirmOptions {
  title: string;
  body?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

export interface PromptOptions {
  title: string;
  body?: React.ReactNode;
  placeholder?: string;
  password?: boolean;
  defaultValue?: string;
  /** permite confirmar com o campo vazio (ex.: exportar sem senha) */
  allowEmpty?: boolean;
  confirmLabel?: string;
  cancelLabel?: string;
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;
type PromptFn = (opts: PromptOptions) => Promise<string | null>;

const ConfirmCtx = createContext<ConfirmFn>(async () => false);
const PromptCtx = createContext<PromptFn>(async () => null);

/** Hook para pedir uma confirmação com modal custom (substitui window.confirm). */
export function useConfirm(): ConfirmFn {
  return useContext(ConfirmCtx);
}

/** Hook para pedir um texto/senha com modal custom (substitui window.prompt).
 *  Resolve com o valor digitado, ou null se cancelar. */
export function usePrompt(): PromptFn {
  return useContext(PromptCtx);
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<(ConfirmOptions & { resolve: (v: boolean) => void }) | null>(null);
  const [prompt, setPrompt] = useState<(PromptOptions & { resolve: (v: string | null) => void }) | null>(null);

  const confirm = useCallback<ConfirmFn>(
    (opts) => new Promise<boolean>((resolve) => setState({ ...opts, resolve })),
    [],
  );
  const promptFn = useCallback<PromptFn>(
    (opts) => new Promise<string | null>((resolve) => setPrompt({ ...opts, resolve })),
    [],
  );

  const close = (v: boolean) => {
    state?.resolve(v);
    setState(null);
  };
  const closePrompt = (v: string | null) => {
    prompt?.resolve(v);
    setPrompt(null);
  };

  return (
    <ConfirmCtx.Provider value={confirm}>
      <PromptCtx.Provider value={promptFn}>
        {children}
        {state && (
          <ConfirmModal
            {...state}
            onCancel={() => close(false)}
            onConfirm={() => close(true)}
          />
        )}
        {prompt && (
          <PromptModal
            {...prompt}
            onCancel={() => closePrompt(null)}
            onConfirm={(v) => closePrompt(v)}
          />
        )}
      </PromptCtx.Provider>
    </ConfirmCtx.Provider>
  );
}

function PromptModal({
  title,
  body,
  placeholder,
  password,
  defaultValue = "",
  allowEmpty = false,
  confirmLabel = "OK",
  cancelLabel = "Cancelar",
  onCancel,
  onConfirm,
}: PromptOptions & { onCancel: () => void; onConfirm: (v: string) => void }) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);
  const canSubmit = allowEmpty || value.trim().length > 0;
  const submit = () => { if (canSubmit) onConfirm(value); };

  return (
    <div
      onClick={onCancel}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="animate-pop w-full max-w-sm rounded-2xl border border-border bg-surface p-5 shadow-2xl"
      >
        <p className="text-base font-semibold text-ink">{title}</p>
        {body && <div className="mt-1.5 text-sm text-ink-soft">{body}</div>}
        <input
          ref={inputRef}
          type={password ? "password" : "text"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); submit(); }
            else if (e.key === "Escape") { e.preventDefault(); onCancel(); }
          }}
          placeholder={placeholder}
          className="mt-4 w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
        />
        <div className="mt-5 flex justify-end gap-2.5">
          <button
            onClick={onCancel}
            className="rounded-full border border-border px-5 py-2 text-sm text-ink transition-colors hover:bg-hover"
          >
            {cancelLabel}
          </button>
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-full bg-ink px-5 py-2 text-sm font-medium text-bg transition-colors hover:opacity-90 disabled:opacity-40"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function ConfirmModal({
  title,
  body,
  confirmLabel = "Confirmar",
  cancelLabel = "Cancelar",
  danger = false,
  onCancel,
  onConfirm,
}: ConfirmOptions & { onCancel: () => void; onConfirm: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
      else if (e.key === "Enter") onConfirm();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel, onConfirm]);

  return (
    <div
      onClick={onCancel}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="animate-pop w-full max-w-sm rounded-2xl border border-border bg-surface p-5 shadow-2xl"
      >
        <p className="text-base font-semibold text-ink">{title}</p>
        {body && <div className="mt-1.5 text-sm text-ink-soft">{body}</div>}
        <div className="mt-5 flex justify-end gap-2.5">
          <button
            onClick={onCancel}
            className="rounded-full border border-border px-5 py-2 text-sm text-ink transition-colors hover:bg-hover"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            autoFocus
            className={`rounded-full px-5 py-2 text-sm font-medium transition-colors ${
              danger ? "bg-red-500 text-white hover:bg-red-600" : "bg-ink text-bg hover:opacity-90"
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
