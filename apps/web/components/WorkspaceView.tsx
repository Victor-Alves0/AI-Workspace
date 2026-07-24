"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft, BarChart3, BookOpen, Box, Brain, Check, Code2, Copy, Download, FileText,
  LayoutGrid, MoreHorizontal, Pencil, Plug, Plus, Search, Settings, Sparkles,
  Gauge, Terminal, Trash2, Upload, Waypoints, Wrench, X,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import type { ModelConfig, Prompt, Skill, SkillSuggestion, Tool, User } from "@/lib/types";
import { AnchoredMenu, MenuItem } from "@/components/ui";
import ToolEditor from "./ToolEditor";
import ModelEditor from "./ModelEditor";
import PromptEditor from "./PromptEditor";
import SkillEditor from "./SkillEditor";
import ValvesModal from "./ValvesModal";
import AnalyticsView from "./AnalyticsView";
import MemoryView from "./MemoryView";
import KnowledgeView from "./KnowledgePanel";
import CodespacePanel from "./CodespacePanel";
import ApiView from "./ApiView";
import ObservabilityView from "./ObservabilityView";

export type Section =
  | "Modelos" | "Conhecimento" | "Cerebros" | "Prompts" | "Skills"
  | "Ferramentas" | "Apps" | "Codespace" | "Memoria" | "Analítica" | "API"
  | "Observabilidade";

// meta dos cards da grade inicial (a contagem é injetada em runtime).
// `admin: true` só aparece para administradores (filtrado em runtime).
const CARD_META: { key: Section; name: string; desc: string; icon: ReactNode; live: boolean; admin?: boolean }[] = [
  { key: "Modelos", name: "Modelos", desc: "Seus modelos e presets de IA", icon: <Box size={22} />, live: true },
  { key: "Ferramentas", name: "Ferramentas", desc: "Tools e integrações MCP", icon: <Wrench size={22} />, live: true },
  { key: "Prompts", name: "Prompts", desc: "Atalhos de comando reutilizáveis", icon: <FileText size={22} />, live: true },
  { key: "Skills", name: "Skills", desc: "Habilidades do agente", icon: <Sparkles size={22} />, live: true },
  { key: "Conhecimento", name: "Conhecimento", desc: "Banco de dados organizados", icon: <BookOpen size={22} />, live: true },
  { key: "Cerebros", name: "Cérebros", desc: "Notas interligadas da IA", icon: <Waypoints size={22} />, live: true },
  { key: "Apps", name: "Apps", desc: "Mini-aplicações e automações", icon: <LayoutGrid size={22} />, live: false },
  { key: "Codespace", name: "Codespace", desc: "Programe com IA", icon: <Code2 size={22} />, live: true },
  { key: "Memoria", name: "Memória", desc: "O que a IA lembra de você", icon: <Brain size={22} />, live: true },
  { key: "Analítica", name: "Analítica", desc: "Uso, custos e desempenho", icon: <BarChart3 size={22} />, live: true },
  { key: "API", name: "API", desc: "Use seus modelos em qualquer app", icon: <Terminal size={22} />, live: true },
  { key: "Observabilidade", name: "Observabilidade", desc: "Cada chamada, latência e query", icon: <Gauge size={22} />, live: true, admin: true },
];

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-accent" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

/* card quadrado da grade inicial */
function WsCard({ icon, name, desc, live, onClick }: {
  icon: ReactNode; name: string; desc: string; live: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="group relative flex h-[168px] flex-col items-center justify-center gap-3 rounded-2xl border border-border bg-surface p-5 text-center transition-all duration-150 hover:-translate-y-0.5 hover:border-accent/40 hover:bg-hover hover:shadow-sm"
    >
      <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
        {icon}
      </span>
      <div className="flex flex-col items-center">
        <p className="text-sm font-semibold text-ink">{name}</p>
        <p className="mt-0.5 line-clamp-2 text-xs leading-4 text-muted">{desc}</p>
      </div>
      {/* selo fora do fluxo → não desloca o centro do ícone+texto */}
      {!live && (
        <span className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-surface2 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">
          Em breve
        </span>
      )}
    </button>
  );
}

/* casca de uma seção: voltar + título + ações */
function SectionShell({ title, count, onBack, actions, children }: {
  title: string; count?: number; onBack: () => void; actions?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="px-4 py-5 md:px-8 md:py-6">
      {/* breadcrumb: Espaço de Trabalho › seção atual */}
      <nav className="mb-4 flex items-center gap-1.5 text-sm">
        <button onClick={onBack} className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-muted transition-colors hover:bg-hover hover:text-ink">
          <ArrowLeft size={16} /> Espaço de Trabalho
        </button>
        <span className="text-muted">/</span>
        <span className="font-medium text-ink">{title}</span>
      </nav>
      {/* mobile: as ações quebram de linha inteiras (sem amassar os botões) */}
      <div className="mb-5 flex flex-wrap items-center justify-between gap-x-3 gap-y-2.5">
        <h1 className="text-2xl font-bold text-ink">
          {title}{count !== undefined && <span className="ml-2 font-semibold text-muted">{count}</span>}
        </h1>
        {actions && <div className="flex flex-wrap items-center gap-2 text-sm">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

function SearchBar({ value, onChange, placeholder }: {
  value: string; onChange: (v: string) => void; placeholder: string;
}) {
  return (
    <div className="mb-4 flex items-center gap-2 rounded-xl border border-border bg-surface px-3.5 py-2.5">
      <Search size={16} className="text-muted" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
      />
    </div>
  );
}

const BTN_GHOST = "whitespace-nowrap rounded-full border border-border bg-surface px-4 py-1.5 text-ink-soft transition-colors hover:bg-surface2";
const BTN_PRIMARY = "flex items-center gap-1.5 whitespace-nowrap rounded-full bg-accent px-4 py-1.5 font-medium text-white transition-colors hover:bg-accent-hover";

function EmptyState({ text }: { text: string }) {
  return <p className="rounded-2xl border border-dashed border-border px-4 py-10 text-center text-sm text-muted">{text}</p>;
}

/* placeholder desenhado p/ seções ainda não implementadas */
function ComingSoon({ icon, title, desc }: { icon: ReactNode; title: string; desc: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-surface py-24 text-center">
      <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-surface2 text-accent-hover">{icon}</span>
      <h2 className="text-lg font-semibold text-ink">{title}</h2>
      <p className="mt-1.5 max-w-sm text-sm leading-5 text-muted">{desc}</p>
      <span className="mt-5 rounded-full bg-surface2 px-3 py-1 text-xs text-muted">Em breve</span>
    </div>
  );
}

const CARD_ROW = "group relative flex items-center gap-3 rounded-2xl border border-border bg-surface p-3.5 transition-all duration-150 hover:border-accent/40 hover:bg-hover";

export default function WorkspaceView({
  onClose,
  initialEditModel,
  initialSection,
  onModelsChanged,
  onOpenChat,
}: {
  onClose: () => void;
  /** se vier um modelo, abre direto o editor dele (ex.: "Editar" no seletor) */
  initialEditModel?: ModelConfig | null;
  /** abre direto numa seção (ex.: atalho "Analítica" no menu do usuário) */
  initialSection?: Section | null;
  /** propaga a lista de modelos para o pai a cada mudança (salvar/toggle/excluir),
   *  para o chat refletir edições no mostrador de ferramentas sem depender do F5. */
  onModelsChanged?: (models: ModelConfig[]) => void;
  /** Codespace: abre um chat (novo ou existente) no pai; `prefill` (opcional)
   *  pré-preenche o composer — usado por "Referenciar no chat" na aba Arquivos. */
  onOpenChat?: (chatId: string, prefill?: string) => void;
}) {
  const router = useRouter();
  // null = grade inicial de cards; senão a seção aberta
  const [section, setSection] = useState<Section | null>(initialSection ?? null);
  const [user, setUser] = useState<User | null>(null);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  // propostas de skill do Aprendizado Proativo (Curator) — aguardando aprovação
  const [proposals, setProposals] = useState<SkillSuggestion[]>([]);
  const [q, setQ] = useState("");
  // filtro por tag na aba Ferramentas: null = todas; "__none__" = sem tags
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const menuBtnRef = useRef<HTMLButtonElement>(null);
  // editingTool: undefined = não editando; null = nova; Tool = editar. valvesTool: engrenagem
  const [editingTool, setEditingTool] = useState<Tool | null | undefined>(undefined);
  const [valvesTool, setValvesTool] = useState<Tool | null>(null);
  const [editingModel, setEditingModel] = useState<ModelConfig | null | undefined>(initialEditModel ?? undefined);
  const [editingPrompt, setEditingPrompt] = useState<Prompt | null | undefined>(undefined);
  const [editingSkill, setEditingSkill] = useState<Skill | null | undefined>(undefined);

  const loadModels = () =>
    api
      .get<ModelConfig[]>("/models")
      .then((m) => { setModels(m); onModelsChanged?.(m); })
      .catch(() => {});
  const loadTools = () => api.get<Tool[]>("/tools").then(setTools).catch(() => {});
  const loadPrompts = () => api.get<Prompt[]>("/prompts").then(setPrompts).catch(() => {});
  const loadSkills = () => api.get<Skill[]>("/skills").then(setSkills).catch(() => {});
  const loadProposals = () => api.get<SkillSuggestion[]>("/skills/proposals").then(setProposals).catch(() => {});

  useEffect(() => {
    api.get<User>("/auth/me").then(setUser).catch((e) => {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
    });
    loadModels();
    loadTools();
    loadPrompts();
    loadSkills();
    loadProposals();
  }, []);

  async function approveProposal(id: string) {
    await api.post(`/skills/proposals/${id}/approve`).catch(() => {});
    loadProposals();
    loadSkills();
  }
  async function dismissProposal(id: string) {
    await api.post(`/skills/proposals/${id}/dismiss`).catch(() => {});
    loadProposals();
  }

  // se o pai pediu p/ abrir o editor de um modelo, já entra na seção Modelos
  useEffect(() => {
    if (initialEditModel !== undefined && initialEditModel !== null) setSection("Modelos");
  }, [initialEditModel]);

  // atalho externo p/ uma seção (ex.: "Analítica" no menu) mesmo com o workspace já aberto
  useEffect(() => {
    if (initialSection) setSection(initialSection);
  }, [initialSection]);

  const author = user ? user.email.split("@")[0] : "";

  function openSection(s: Section) {
    setSection(s);
    setQ("");
    setTagFilter(null);
  }
  function backHome() {
    setSection(null);
    setQ("");
    setTagFilter(null);
  }

  async function toggleModel(m: ModelConfig) {
    await api.patch(`/models/${m.id}`, { enabled: !m.enabled });
    loadModels();
  }
  async function deleteModel(id: string) {
    await api.del(`/models/${id}`);
    loadModels();
  }
  async function toggleTool(t: Tool) {
    await api.patch(`/tools/${t.id}`, { enabled: !t.enabled });
    loadTools();
  }
  async function deleteTool(id: string) {
    await api.del(`/tools/${id}`);
    loadTools();
  }
  async function cloneTool(t: Tool) {
    await api.post("/tools", {
      path: `${t.path}_copia`,
      name: `${t.name || t.path} (cópia)`,
      description: t.description,
      params: t.params,
      returns: t.returns,
      code: t.code,
      valves: t.valves,
      enabled: t.enabled,
      tags: t.tags ?? [],
      tool_type: t.tool_type ?? "code",
      mcp_config: t.mcp_config ?? {},
    });
    loadTools();
  }
  function exportTool(t: Tool) {
    const data = { path: t.path, name: t.name, description: t.description, params: t.params, returns: t.returns, code: t.code, valves: t.valves, tags: t.tags ?? [], tool_type: t.tool_type ?? "code", mcp_config: t.mcp_config ?? {} };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${t.path || "ferramenta"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function exportModels() {
    const blob = new Blob([JSON.stringify(models, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "modelos.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function deletePrompt(id: string) {
    await api.del(`/prompts/${id}`);
    loadPrompts();
  }

  async function deleteSkill(id: string) {
    await api.del(`/skills/${id}`);
    loadSkills();
  }
  async function toggleSkill(s: Skill) {
    await api.patch(`/skills/${s.id}`, { enabled: !s.enabled });
    loadSkills();
  }
  async function cloneSkill(s: Skill) {
    await api.post("/skills", {
      slug: `${s.slug}_copia`,
      name: `${s.name} (cópia)`,
      description: s.description,
      content: s.content,
      tags: s.tags ?? [],
      enabled: s.enabled,
    });
    loadSkills();
  }
  function exportSkills() {
    const data = skills.map((s) => ({ slug: s.slug, name: s.name, description: s.description, content: s.content, tags: s.tags ?? [] }));
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "skills.json";
    a.click();
    URL.revokeObjectURL(url);
  }
  function exportPrompts() {
    const data = prompts.map((p) => ({ command: p.command, title: p.title, content: p.content }));
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "prompts.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  const filteredModels = useMemo(
    () => models.filter((m) => m.name.toLowerCase().includes(q.toLowerCase())),
    [models, q],
  );
  // tags distintas entre as ferramentas (para a barra de filtros)
  const allTags = useMemo(() => {
    const s = new Set<string>();
    for (const t of tools) for (const tag of t.tags ?? []) s.add(tag);
    return Array.from(s).sort();
  }, [tools]);
  const hasUntagged = useMemo(() => tools.some((t) => !(t.tags ?? []).length), [tools]);

  const filteredTools = useMemo(
    () =>
      tools.filter((t) => {
        if (!(t.name || t.path).toLowerCase().includes(q.toLowerCase())) return false;
        if (tagFilter === null) return true;
        const tags = t.tags ?? [];
        return tagFilter === "__none__" ? tags.length === 0 : tags.includes(tagFilter);
      }),
    [tools, q, tagFilter],
  );
  const filteredPrompts = useMemo(
    () => prompts.filter((p) => `${p.title} ${p.command}`.toLowerCase().includes(q.toLowerCase())),
    [prompts, q],
  );
  const filteredSkills = useMemo(
    () => skills.filter((s) => `${s.name} ${s.slug} ${s.description}`.toLowerCase().includes(q.toLowerCase())),
    [skills, q],
  );

  // editores em tela cheia (mantêm a sidebar quando embutidos no chat)
  if (editingTool !== undefined) {
    return (
      <ToolEditor
        tool={editingTool}
        onClose={() => setEditingTool(undefined)}
        onSaved={() => { setEditingTool(undefined); loadTools(); }}
      />
    );
  }
  if (editingModel !== undefined) {
    return (
      <ModelEditor
        model={editingModel}
        onClose={() => setEditingModel(undefined)}
        onSaved={() => { setEditingModel(undefined); loadModels(); }}
      />
    );
  }
  if (editingPrompt !== undefined) {
    return (
      <PromptEditor
        prompt={editingPrompt}
        onClose={() => setEditingPrompt(undefined)}
        onSaved={() => { setEditingPrompt(undefined); loadPrompts(); }}
      />
    );
  }
  if (editingSkill !== undefined) {
    return (
      <SkillEditor
        skill={editingSkill}
        onClose={() => setEditingSkill(undefined)}
        onSaved={() => { setEditingSkill(undefined); loadSkills(); }}
      />
    );
  }

  return (
    <div className="h-full flex-1 overflow-y-auto bg-bg">
      {/* ------------------------------ grade inicial ----------------------------- */}
      {section === null && (
        <div className="mx-auto max-w-5xl px-4 py-6 md:px-8 md:py-8">
          <div className="mb-6 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-2xl font-bold text-ink">Espaço de Trabalho</h1>
              <p className="mt-1 text-sm text-muted">Tudo que personaliza sua IA, num só lugar.</p>
            </div>
            <button onClick={onClose} className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">
              <ArrowLeft size={16} /> Voltar ao chat
            </button>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {CARD_META.filter((c) => !c.admin || user?.role === "admin").map((c) => (
              <WsCard
                key={c.key}
                icon={c.icon}
                name={c.name}
                desc={c.desc}
                live={c.live}
                onClick={() => openSection(c.key)}
              />
            ))}
          </div>
        </div>
      )}

      {/* --------------------------------- Modelos -------------------------------- */}
      {section === "Modelos" && (
        <SectionShell
          title="Modelos"
          count={models.length}
          onBack={backHome}
          actions={
            <>
              <button className={BTN_GHOST} onClick={() => alert("Importar: em breve")}><Upload size={14} className="mr-1.5 inline" />Importar</button>
              <button className={BTN_GHOST} onClick={exportModels}><Download size={14} className="mr-1.5 inline" />Exportar</button>
              <button className={BTN_PRIMARY} onClick={() => setEditingModel(null)}><Plus size={15} />Novo Modelo</button>
            </>
          }
        >
          <SearchBar value={q} onChange={setQ} placeholder="Pesquisar modelos" />
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            {filteredModels.map((m) => (
              <div key={m.id} className={CARD_ROW}>
                {m.avatar_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={m.avatar_url} alt="" className="h-10 w-10 rounded-full object-cover" />
                ) : (
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-surface2 text-ink">
                    <Box size={17} />
                  </span>
                )}
                <button onClick={() => setEditingModel(m)} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-sm font-medium text-ink">{m.name}</p>
                  <p className="truncate text-xs text-muted">Por {author} • {m.base_model}</p>
                </button>
                <div className="relative">
                  <button ref={menu === m.id ? menuBtnRef : undefined} onClick={() => setMenu(menu === m.id ? null : m.id)} className={`rounded p-1 text-muted hover:text-ink ${menu === m.id ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
                    <MoreHorizontal size={18} />
                  </button>
                  {menu === m.id && (
                    <AnchoredMenu anchorRef={menuBtnRef} onClose={() => setMenu(null)} align="left">
                      <MenuItem icon={<Pencil size={15} />} onClick={() => { setEditingModel(m); setMenu(null); }}>Editar</MenuItem>
                      <MenuItem danger icon={<Trash2 size={15} />} onClick={() => { deleteModel(m.id); setMenu(null); }}>Excluir</MenuItem>
                    </AnchoredMenu>
                  )}
                </div>
                <Toggle on={m.enabled} onClick={() => toggleModel(m)} />
              </div>
            ))}
          </div>
          {filteredModels.length === 0 && <EmptyState text={q ? "Nenhum modelo encontrado." : "Nenhum modelo ainda. Clique em “Novo Modelo”."} />}
        </SectionShell>
      )}

      {/* ------------------------------- Ferramentas ------------------------------ */}
      {section === "Ferramentas" && (
        <SectionShell
          title="Ferramentas"
          count={tools.length}
          onBack={backHome}
          actions={<button className={BTN_PRIMARY} onClick={() => setEditingTool(null)}><Plus size={15} />Nova Ferramenta</button>}
        >
          <SearchBar value={q} onChange={setQ} placeholder="Pesquisar ferramentas" />

          {/* filtros por tag */}
          {(allTags.length > 0 || hasUntagged) && (
            <div className="mb-4 flex flex-wrap items-center gap-1.5">
              <button
                onClick={() => setTagFilter(null)}
                className={`rounded-full px-3 py-1 text-xs transition-colors ${tagFilter === null ? "bg-accent/15 font-medium text-accent-hover" : "border border-border text-muted hover:text-ink"}`}
              >
                Todas
              </button>
              {allTags.map((tag) => (
                <button
                  key={tag}
                  onClick={() => setTagFilter(tagFilter === tag ? null : tag)}
                  className={`rounded-full px-3 py-1 text-xs transition-colors ${tagFilter === tag ? "bg-accent/15 font-medium text-accent-hover" : "border border-border text-muted hover:text-ink"}`}
                >
                  {tag}
                </button>
              ))}
              {hasUntagged && allTags.length > 0 && (
                <button
                  onClick={() => setTagFilter(tagFilter === "__none__" ? null : "__none__")}
                  className={`rounded-full px-3 py-1 text-xs transition-colors ${tagFilter === "__none__" ? "bg-accent/15 font-medium text-accent-hover" : "border border-border text-muted hover:text-ink"}`}
                >
                  Sem tags
                </button>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            {filteredTools.map((t) => (
              <div key={t.id} className={CARD_ROW}>
                <span
                  title={t.tool_type === "mcp" ? "Integração (MCP)" : "Código"}
                  className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${t.tool_type === "mcp" ? "bg-accent/15 text-accent-hover" : "bg-surface2 text-ink-soft"}`}
                >
                  {t.tool_type === "mcp" ? <Plug size={17} /> : <Code2 size={17} />}
                </span>
                <button onClick={() => setEditingTool(t)} className="min-w-0 flex-1 text-left">
                  <p className="flex items-center gap-2 truncate text-sm font-medium text-ink">
                    <span className="truncate">{t.name || t.path}</span>
                    {(t.tags ?? []).slice(0, 3).map((tag) => (
                      <span key={tag} className="shrink-0 rounded-full bg-surface2 px-2 py-0.5 text-[10px] font-normal text-muted">{tag}</span>
                    ))}
                  </p>
                  <p className="truncate font-mono text-xs text-muted">Por {author} • {t.path}</p>
                </button>
                <button onClick={() => setValvesTool(t)} title="Configurações" className="rounded p-1 text-muted opacity-0 hover:text-ink group-hover:opacity-100">
                  <Settings size={17} />
                </button>
                <div className="relative">
                  <button ref={menu === t.id ? menuBtnRef : undefined} onClick={() => setMenu(menu === t.id ? null : t.id)} className={`rounded p-1 text-muted hover:text-ink ${menu === t.id ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
                    <MoreHorizontal size={18} />
                  </button>
                  {menu === t.id && (
                    <AnchoredMenu anchorRef={menuBtnRef} onClose={() => setMenu(null)} align="left">
                      <MenuItem icon={<Pencil size={15} />} onClick={() => { setEditingTool(t); setMenu(null); }}>Editar</MenuItem>
                      <MenuItem icon={<Copy size={15} />} onClick={() => { cloneTool(t); setMenu(null); }}>Clonar</MenuItem>
                      <MenuItem icon={<Download size={15} />} onClick={() => { exportTool(t); setMenu(null); }}>Exportar</MenuItem>
                      <MenuItem danger icon={<Trash2 size={15} />} onClick={() => { deleteTool(t.id); setMenu(null); }}>Excluir</MenuItem>
                    </AnchoredMenu>
                  )}
                </div>
                <Toggle on={t.enabled} onClick={() => toggleTool(t)} />
              </div>
            ))}
          </div>
          {filteredTools.length === 0 && <EmptyState text={q ? "Nenhuma ferramenta encontrada." : "Nenhuma ferramenta ainda. Clique em “Nova Ferramenta”."} />}
        </SectionShell>
      )}

      {/* --------------------------------- Prompts -------------------------------- */}
      {section === "Prompts" && (
        <SectionShell
          title="Prompts"
          count={prompts.length}
          onBack={backHome}
          actions={
            <>
              <button className={BTN_GHOST} onClick={() => alert("Importar: em breve")}><Upload size={14} className="mr-1.5 inline" />Importar</button>
              <button className={BTN_GHOST} onClick={exportPrompts}><Download size={14} className="mr-1.5 inline" />Exportar</button>
              <button className={BTN_PRIMARY} onClick={() => setEditingPrompt(null)}><Plus size={15} />Novo Prompt</button>
            </>
          }
        >
          <SearchBar value={q} onChange={setQ} placeholder="Pesquisar prompts" />
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            {filteredPrompts.map((p) => (
              <div key={p.id} className={CARD_ROW}>
                <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-surface2 text-ink"><FileText size={17} /></span>
                <button onClick={() => setEditingPrompt(p)} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-sm font-medium text-ink">
                    {p.title} <span className="font-mono text-xs text-muted">/{p.command}</span>
                  </p>
                  <p className="truncate text-xs text-muted">Por {author} • {p.content}</p>
                </button>
                <button
                  onClick={() => void copyText(p.content)}
                  title="Copiar conteúdo"
                  className="rounded p-1 text-muted opacity-0 hover:text-ink group-hover:opacity-100"
                >
                  <Copy size={16} />
                </button>
                <div className="relative">
                  <button ref={menu === p.id ? menuBtnRef : undefined} onClick={() => setMenu(menu === p.id ? null : p.id)} className={`rounded p-1 text-muted hover:text-ink ${menu === p.id ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
                    <MoreHorizontal size={18} />
                  </button>
                  {menu === p.id && (
                    <AnchoredMenu anchorRef={menuBtnRef} onClose={() => setMenu(null)} align="left">
                      <MenuItem icon={<Pencil size={15} />} onClick={() => { setEditingPrompt(p); setMenu(null); }}>Editar</MenuItem>
                      <MenuItem danger icon={<Trash2 size={15} />} onClick={() => { deletePrompt(p.id); setMenu(null); }}>Excluir</MenuItem>
                    </AnchoredMenu>
                  )}
                </div>
              </div>
            ))}
          </div>
          {filteredPrompts.length === 0 && <EmptyState text={q ? "Nenhum prompt encontrado." : "Nenhum prompt ainda. Clique em “Novo Prompt”."} />}
        </SectionShell>
      )}

      {/* ---------------------------------- Skills -------------------------------- */}
      {section === "Skills" && (
        <SectionShell
          title="Skills"
          count={skills.length}
          onBack={backHome}
          actions={
            <>
              <button className={BTN_GHOST} onClick={exportSkills}><Download size={14} className="mr-1.5 inline" />Exportar</button>
              <button className={BTN_PRIMARY} onClick={() => setEditingSkill(null)}><Plus size={15} />Nova Skill</button>
            </>
          }
        >
          {proposals.length > 0 && (
            <div className="mb-4 rounded-2xl border border-accent/30 bg-accent/5 p-3">
              <p className="mb-2 flex items-center gap-1.5 px-1 text-sm font-medium text-ink">
                <Sparkles size={15} className="text-accent-hover" />
                Sugeridas pela IA
                <span className="rounded-full bg-accent/20 px-2 py-0.5 text-[11px] text-accent-hover">{proposals.length}</span>
              </p>
              <p className="mb-2.5 px-1 text-xs text-muted">O Aprendizado Proativo notou estes padrões nas suas conversas. Aprove para virar uma skill.</p>
              <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                {proposals.map((p) => (
                  <div key={p.id} className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-3">
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 text-sm font-medium text-ink">
                        <span className="truncate">{p.name}</span>
                        {(p.tags ?? []).slice(0, 3).map((tag) => (
                          <span key={tag} className="shrink-0 rounded-full bg-surface2 px-2 py-0.5 text-[10px] font-normal text-muted">{tag}</span>
                        ))}
                      </p>
                      <p className="mt-0.5 line-clamp-2 text-xs text-muted">{p.description || "Sem descrição"}</p>
                    </div>
                    <div className="flex items-center justify-end gap-2">
                      <button onClick={() => dismissProposal(p.id)} className="flex items-center gap-1 rounded-full border border-border px-3 py-1 text-xs text-muted transition-colors hover:text-ink">
                        <X size={13} /> Descartar
                      </button>
                      <button onClick={() => approveProposal(p.id)} className="flex items-center gap-1 rounded-full bg-accent px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-accent-hover">
                        <Check size={13} /> Aprovar
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <SearchBar value={q} onChange={setQ} placeholder="Pesquisar skills" />
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            {filteredSkills.map((s) => (
              <div key={s.id} className={CARD_ROW}>
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent-hover">
                  <Sparkles size={17} />
                </span>
                <button onClick={() => setEditingSkill(s)} className="min-w-0 flex-1 text-left">
                  <p className="flex items-center gap-2 truncate text-sm font-medium text-ink">
                    <span className="truncate">{s.name}</span>
                    <span className="shrink-0 font-mono text-xs text-muted">${s.slug}</span>
                    {(s.tags ?? []).slice(0, 3).map((tag) => (
                      <span key={tag} className="shrink-0 rounded-full bg-surface2 px-2 py-0.5 text-[10px] font-normal text-muted">{tag}</span>
                    ))}
                  </p>
                  <p className="truncate text-xs text-muted">{s.description || "Sem descrição"}</p>
                </button>
                <div className="relative">
                  <button ref={menu === s.id ? menuBtnRef : undefined} onClick={() => setMenu(menu === s.id ? null : s.id)} className={`rounded p-1 text-muted hover:text-ink ${menu === s.id ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
                    <MoreHorizontal size={18} />
                  </button>
                  {menu === s.id && (
                    <AnchoredMenu anchorRef={menuBtnRef} onClose={() => setMenu(null)} align="left">
                      <MenuItem icon={<Pencil size={15} />} onClick={() => { setEditingSkill(s); setMenu(null); }}>Editar</MenuItem>
                      <MenuItem icon={<Copy size={15} />} onClick={() => { cloneSkill(s); setMenu(null); }}>Clonar</MenuItem>
                      <MenuItem danger icon={<Trash2 size={15} />} onClick={() => { deleteSkill(s.id); setMenu(null); }}>Excluir</MenuItem>
                    </AnchoredMenu>
                  )}
                </div>
                <Toggle on={s.enabled} onClick={() => toggleSkill(s)} />
              </div>
            ))}
          </div>
          {filteredSkills.length === 0 && <EmptyState text={q ? "Nenhuma skill encontrada." : "Nenhuma skill ainda. Clique em “Nova Skill”."} />}
        </SectionShell>
      )}

      {/* --------------------------- seções ainda por vir ------------------------- */}
      {section === "Conhecimento" && (
        <SectionShell title="Conhecimento" onBack={backHome}>
          <KnowledgeView />
        </SectionShell>
      )}
      {section === "Cerebros" && (
        <SectionShell title="Cérebros" onBack={backHome}>
          <KnowledgeView kind="brain" />
        </SectionShell>
      )}
      {section === "Apps" && (
        <SectionShell title="Apps" onBack={backHome}>
          <ComingSoon icon={<LayoutGrid size={26} />} title="Apps" desc="Mini-aplicações e fluxos prontos para instalar no seu workspace." />
        </SectionShell>
      )}
      {section === "Codespace" && (
        <SectionShell title="Codespace" onBack={backHome}>
          <CodespacePanel onOpenChat={(chatId, prefill) => { onOpenChat?.(chatId, prefill); onClose(); }} />
        </SectionShell>
      )}
      {section === "Memoria" && (
        <SectionShell title="Memória" onBack={backHome}>
          <MemoryView />
        </SectionShell>
      )}
      {section === "Analítica" && (
        <SectionShell title="Analítica" onBack={backHome}>
          <AnalyticsView />
        </SectionShell>
      )}
      {section === "API" && (
        <SectionShell title="API" onBack={backHome}>
          <ApiView />
        </SectionShell>
      )}
      {section === "Observabilidade" && (
        <SectionShell title="Observabilidade" onBack={backHome}>
          <ObservabilityView />
        </SectionShell>
      )}

      {valvesTool && (
        <ValvesModal tool={valvesTool} onClose={() => setValvesTool(null)} onSaved={loadTools} />
      )}
    </div>
  );
}
