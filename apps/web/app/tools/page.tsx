"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { Tool } from "@/lib/types";

const EMPTY = {
  path: "",
  name: "",
  description: "",
  params: "{}",
  returns: "[]",
  code: 'def run(**params):\n    """Sua ferramenta."""\n    return {"echo": params}\n',
  enabled: true,
};

export default function ToolsPage() {
  const router = useRouter();
  const [tools, setTools] = useState<Tool[]>([]);
  const [sel, setSel] = useState<string | "new" | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [test, setTest] = useState<{ args: string; out: string }>({ args: "{}", out: "" });
  const [err, setErr] = useState<string | null>(null);

  const reload = () =>
    api
      .get<Tool[]>("/tools")
      .then(setTools)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
      });

  useEffect(() => {
    reload();
  }, []);

  function edit(t: Tool) {
    setSel(t.id);
    setErr(null);
    setForm({
      path: t.path,
      name: t.name,
      description: t.description,
      params: JSON.stringify(t.params, null, 2),
      returns: JSON.stringify(t.returns),
      code: t.code,
      enabled: t.enabled,
    });
  }

  function startNew() {
    setSel("new");
    setErr(null);
    setForm({ ...EMPTY });
    setTest({ args: "{}", out: "" });
  }

  async function save() {
    setErr(null);
    let params: unknown, returns: unknown;
    try {
      params = JSON.parse(form.params || "{}");
      returns = JSON.parse(form.returns || "[]");
    } catch {
      setErr("params/returns devem ser JSON válido");
      return;
    }
    const body = {
      path: form.path,
      name: form.name,
      description: form.description,
      params,
      returns,
      code: form.code,
      enabled: form.enabled,
    };
    try {
      if (sel === "new") await api.post("/tools", body);
      else await api.patch(`/tools/${sel}`, body);
      await reload();
      setSel(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    }
  }

  async function remove(id: string) {
    await api.del(`/tools/${id}`);
    await reload();
    if (sel === id) setSel(null);
  }

  async function runTest() {
    let args: unknown;
    try {
      args = JSON.parse(test.args || "{}");
    } catch {
      setTest((t) => ({ ...t, out: "args inválidos (use JSON)" }));
      return;
    }
    const r = await api.post<{ ok: boolean; result?: unknown; error?: string }>(
      "/tools/test",
      { code: form.code, params: args },
    );
    setTest((t) => ({
      ...t,
      out: r.ok ? JSON.stringify(r.result, null, 2) : `Erro: ${r.error}`,
    }));
  }

  return (
    <div className="flex h-screen">
      {/* lista */}
      <aside className="flex w-72 shrink-0 flex-col border-r border-border bg-surface">
        <div className="flex items-center justify-between p-3">
          <button onClick={() => router.push("/chat")} className="text-sm text-muted hover:text-ink-soft">
            ← Chat
          </button>
          <span className="font-semibold">Ferramentas</span>
        </div>
        <div className="px-3">
          <button onClick={startNew} className="w-full rounded-lg bg-accent py-2 text-sm font-medium text-ink">
            + Nova ferramenta
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto p-2">
          {tools.map((t) => (
            <div
              key={t.id}
              className={`group flex items-center justify-between rounded-lg px-2 py-2 ${
                sel === t.id ? "bg-surface2" : "hover:bg-surface2"
              }`}
            >
              <button onClick={() => edit(t)} className="flex-1 text-left">
                <div className="text-sm">{t.name || t.path}</div>
                <div className="font-mono text-xs text-muted">{t.path}</div>
              </button>
              <span className={`ml-2 text-xs ${t.enabled ? "text-green-400" : "text-muted"}`}>
                {t.enabled ? "on" : "off"}
              </span>
              <button onClick={() => remove(t.id)} className="ml-2 hidden text-muted hover:text-red-400 group-hover:block">
                ✕
              </button>
            </div>
          ))}
          {tools.length === 0 && <p className="p-2 text-xs text-muted">Nenhuma ferramenta ainda.</p>}
        </nav>
      </aside>

      {/* editor */}
      <main className="flex-1 overflow-y-auto p-6">
        {sel === null ? (
          <div className="flex h-full items-center justify-center text-muted">
            Selecione ou crie uma ferramenta. O modelo a descobre via SIFT (search/schema/execute).
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1">
                <span className="text-xs text-muted">Caminho (ex.: custom.clima.previsao)</span>
                <input
                  value={form.path}
                  onChange={(e) => setForm({ ...form, path: e.target.value })}
                  className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-sm outline-none focus:border-accent"
                />
              </label>
              <label className="space-y-1">
                <span className="text-xs text-muted">Nome</span>
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm outline-none focus:border-accent"
                />
              </label>
            </div>

            <label className="block space-y-1">
              <span className="text-xs text-muted">Descrição (o modelo usa isto para decidir)</span>
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm outline-none focus:border-accent"
              />
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1">
                <span className="text-xs text-muted">
                  params (JSON, formato SIFT &quot;tipo:o:default:desc&quot;)
                </span>
                <textarea
                  rows={3}
                  value={form.params}
                  onChange={(e) => setForm({ ...form, params: e.target.value })}
                  className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-xs outline-none focus:border-accent"
                />
              </label>
              <label className="space-y-1">
                <span className="text-xs text-muted">returns (JSON array de campos)</span>
                <textarea
                  rows={3}
                  value={form.returns}
                  onChange={(e) => setForm({ ...form, returns: e.target.value })}
                  className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-xs outline-none focus:border-accent"
                />
              </label>
            </div>

            <label className="block space-y-1">
              <span className="text-xs text-muted">Código Python — defina `def run(**params)`</span>
              <textarea
                rows={12}
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                spellCheck={false}
                className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-xs outline-none focus:border-accent"
              />
            </label>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              Habilitada (disponível para os modelos)
            </label>

            {err && <p className="text-sm text-red-400">{err}</p>}

            <div className="flex gap-2">
              <button onClick={save} className="rounded-lg bg-accent px-5 py-2 text-sm font-medium text-ink">
                Salvar
              </button>
              <button onClick={() => setSel(null)} className="rounded-lg border border-border px-4 py-2 text-sm text-muted hover:text-ink-soft">
                Cancelar
              </button>
            </div>

            {/* testar */}
            <div className="rounded-lg border border-border bg-surface p-4">
              <h4 className="mb-2 text-sm font-semibold">Testar execução</h4>
              <div className="flex gap-2">
                <input
                  value={test.args}
                  onChange={(e) => setTest({ ...test, args: e.target.value })}
                  placeholder='{"cidade": "SP"}'
                  className="flex-1 rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-xs outline-none focus:border-accent"
                />
                <button onClick={runTest} className="rounded-lg border border-border px-4 py-2 text-sm hover:border-accent">
                  Rodar
                </button>
              </div>
              {test.out && (
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-surface2 p-3 text-xs">
                  {test.out}
                </pre>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
