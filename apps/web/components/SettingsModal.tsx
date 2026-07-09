"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Blocks,
  Cable,
  ChevronLeft,
  Database,
  AudioLines,
  Home,
  Info,
  Keyboard,
  KeyRound,
  PanelsTopLeft,
  RotateCcw,
  Search,
  Settings,
  Sparkles,
  UserCog,
  CircleUserRound,
  Volume2,
  X,
} from "lucide-react";
import {
  SiDiscord,
  SiGithub,
  SiGoogle,
  SiGoogledrive,
  SiNotion,
  SiOllama,
  SiTelegram,
  SiTrello,
  SiWhatsapp,
} from "react-icons/si";
import { api, ApiError } from "@/lib/api";
import { fileToAvatarDataUrl } from "@/lib/image";
import {
  SHORTCUT_GROUPS, SHORTCUTS, resolveBinding, prettyCombo, eventToCombo, comboHasModifier,
  type ShortcutMap, type ShortcutBinding,
} from "@/lib/shortcuts";
import type { Model, User } from "@/lib/types";
import ArchivedModal from "./ArchivedModal";
import ModelField from "./ModelField";
import GoogleWorkspacePanel from "./GoogleWorkspacePanel";
import TuyaPanel from "./TuyaPanel";
import WhatsAppPanel from "./WhatsAppPanel";
import OllamaPanel from "./OllamaPanel";
import VoicePanel from "./VoicePanel";
import { useConfirm } from "./ConfirmDialog";

type Cat = "general" | "interface" | "connections" | "integrations" | "personalization" | "shortcuts" | "audio" | "data" | "account" | "about";

const CATS: { key: Cat; label: string; icon: React.ReactNode }[] = [
  { key: "general", label: "Geral", icon: <Settings size={16} /> },
  { key: "interface", label: "Interface", icon: <PanelsTopLeft size={16} /> },
  { key: "connections", label: "Conexões", icon: <Cable size={16} /> },
  { key: "integrations", label: "Integrações", icon: <Blocks size={16} /> },
  { key: "personalization", label: "Personalização", icon: <Sparkles size={16} /> },
  { key: "shortcuts", label: "Atalhos", icon: <Keyboard size={16} /> },
  { key: "audio", label: "Áudio", icon: <Volume2 size={16} /> },
  { key: "data", label: "Controle de Dados", icon: <Database size={16} /> },
  { key: "account", label: "Conta", icon: <CircleUserRound size={16} /> },
  { key: "about", label: "Sobre", icon: <Info size={16} /> },
];

// Índice de TODAS as configurações, p/ a busca encontrar um item por nome em
// qualquer categoria (ex.: "Prompt" → "Prompt do Sistema" em Geral). `view`
// aponta o card interno a abrir em Conexões/Integrações (senão cai na grade).
const SETTINGS_INDEX: { label: string; cat: Cat; view?: string }[] = [
  { label: "Tema", cat: "general" },
  { label: "Idioma", cat: "general" },
  { label: "Barra Lateral", cat: "interface", view: "sidebar" },
  { label: "Gerar título de novos chats", cat: "interface", view: "sidebar" },
  { label: "Notificações", cat: "general" },
  { label: "Prompt do Sistema", cat: "personalization" },
  { label: "Parâmetros Avançados", cat: "personalization" },
  { label: "Atalhos de teclado", cat: "shortcuts" },
  { label: "Atalhos", cat: "shortcuts" },
  { label: "Nome", cat: "account" },
  { label: "Sobre você", cat: "account" },
  { label: "Gênero", cat: "account" },
  { label: "Data de nascimento", cat: "account" },
  { label: "Webhook de notificação", cat: "account" },
  { label: "Alterar Senha", cat: "account" },
  { label: "Chaves API", cat: "account" },
  { label: "APIs", cat: "connections", view: "apis" },
  { label: "Voz Local", cat: "connections", view: "voice" },
  { label: "Kokoro", cat: "connections", view: "voice" },
  { label: "Chave do OpenRouter", cat: "connections", view: "apis" },
  { label: "Chave Tavily", cat: "connections", view: "apis" },
  { label: "Chave Brave Search", cat: "connections", view: "apis" },
  { label: "Chave Finnhub", cat: "connections", view: "apis" },
  { label: "Chave Alpha Vantage", cat: "connections", view: "apis" },
  { label: "Chave do provedor de voz", cat: "audio" },
  { label: "Importar Chats", cat: "data" },
  { label: "Exportar Chats", cat: "data" },
  { label: "Chats Arquivados", cat: "data" },
  { label: "Chats compartilhados", cat: "data" },
  { label: "Arquivar Todos os Chats", cat: "data" },
  { label: "Excluir Todos os Chats", cat: "data" },
  { label: "Gerenciar arquivos", cat: "data" },
  { label: "Memória da IA", cat: "data" },
  { label: "Sobre", cat: "about" },
];

const catLabel = (c: Cat) => CATS.find((x) => x.key === c)?.label ?? c;

interface SecretStatus {
  openrouter: boolean;
  tavily: boolean;
  brave: boolean;
  voice: boolean;
  finnhub: boolean;
  alphavantage: boolean;
}

/* ------------------------------- helpers UI ------------------------------- */
function Row({ label, sub, children }: { label: string; sub?: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5 text-sm">
      <div className="min-w-0">
        <p className="font-medium text-ink">{label}</p>
        {sub && <p className="text-xs text-muted">{sub}</p>}
      </div>
      {children}
    </div>
  );
}

function LinkBtn({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button onClick={onClick} className="shrink-0 text-sm text-muted hover:text-ink">
      {children}
    </button>
  );
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-green-500" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return <p className="mb-1 mt-5 text-sm font-semibold text-ink first:mt-0">{children}</p>;
}

/* grade de cards (2 colunas): quadradinho com ícone + nome abaixo */
function CardGrid({
  cards,
  onOpen,
}: {
  cards: { key: string; icon: React.ReactNode; name: string; desc?: string }[];
  onOpen: (key: string) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-3 pt-1">
      {cards.map((c) => (
        <button
          key={c.key}
          onClick={() => onOpen(c.key)}
          className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
        >
          <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
            {c.icon}
          </span>
          <span className="text-sm font-medium text-ink">{c.name}</span>
          {c.desc && <span className="text-xs leading-4 text-muted">{c.desc}</span>}
        </button>
      ))}
    </div>
  );
}

/* cabeçalho de um detalhe com botão Voltar */
function DetailView({ title, onBack, children }: { title: string; onBack: () => void; children: React.ReactNode }) {
  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>
      <Heading>{title}</Heading>
      {children}
    </div>
  );
}

function NumberField({ label, value, onChange, placeholder, suffix }: {
  label: string; value: number | ""; onChange: (v: number | "") => void; placeholder?: string; suffix?: string;
}) {
  return (
    <label className="flex items-center justify-between gap-4 py-2 text-sm">
      <span className="text-ink-soft">{label}</span>
      <span className="flex items-center gap-1.5">
        <input
          type="number"
          value={value}
          onChange={(e) => onChange(e.target.value === "" ? "" : Math.max(0, Number(e.target.value)))}
          placeholder={placeholder}
          className="w-28 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-right text-sm text-ink outline-none focus:border-accent"
        />
        {suffix && <span className="text-xs text-muted">{suffix}</span>}
      </span>
    </label>
  );
}

function SecretField({ label, name, configured, hint, onSaved }: {
  label: string; name: string; configured: boolean; hint?: string; onSaved: () => void;
}) {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  async function save() {
    await api.put(`/settings/secrets/${name}`, { api_key: value });
    setValue(""); setSaved(true); onSaved();
    setTimeout(() => setSaved(false), 1500);
  }
  return (
    <div className="space-y-1.5 py-2">
      <label className="text-sm text-ink-soft">
        {label} {configured && <span className="text-green-400">(configurada)</span>}
      </label>
      <div className="flex gap-2">
        <input
          type="password"
          placeholder="••••••"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="flex-1 rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        />
        <button onClick={save} disabled={!value.trim()} className="rounded-lg bg-accent px-3 py-2 text-sm text-ink disabled:opacity-50">
          {saved ? "✓" : "Salvar"}
        </button>
      </div>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

/* --------------------------------- modal ---------------------------------- */
export default function SettingsModal({ onClose, onSaved, onConnectionsChanged }: { onClose: () => void; onSaved?: () => void; onConnectionsChanged?: () => void }) {
  const router = useRouter();
  const [visible, setVisible] = useState(false);
  const [cat, setCat] = useState<Cat>("general");
  const [q, setQ] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [profile, setProfile] = useState<Record<string, any>>({});
  const [status, setStatus] = useState<SecretStatus | null>(null);
  // card aberto dentro de Conexões / Integrações (null = mostra a grade)
  const [connView, setConnView] = useState<string | null>(null);
  const [integView, setIntegView] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // animação de abrir/fechar
  useEffect(() => {
    setVisible(true);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function close() {
    setVisible(false);
    setTimeout(onClose, 200);
  }

  const reloadSecrets = () => api.get<SecretStatus>("/settings/secrets").then(setStatus).catch(() => {});

  useEffect(() => {
    api.get<User>("/auth/me").then((u) => { setUser(u); setProfile(u.profile ?? {}); }).catch(() => {});
    reloadSecrets();
  }, []);

  const set = (k: string, v: any) => setProfile((p) => ({ ...p, [k]: v }));

  const [saveErr, setSaveErr] = useState<string | null>(null);
  async function saveProfile() {
    setSaveErr(null);
    try {
      await api.put("/settings/profile", profile);
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1500);
      onSaved?.(); // avisa o app p/ atualizar nome/foto na barra lateral
    } catch (e) {
      setSaveErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    }
  }

  // busca profunda: quando há texto, mostra os itens de configuração que casam
  // (de qualquer categoria) no painel à direita, em vez do conteúdo da aba.
  const searchResults = useMemo(() => {
    const f = q.trim().toLowerCase();
    if (!f) return null;
    return SETTINGS_INDEX.filter(
      (s) => s.label.toLowerCase().includes(f) || catLabel(s.cat).toLowerCase().includes(f),
    );
  }, [q]);

  return (
    <div
      onClick={close}
      className={`fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm transition-opacity duration-200 ${visible ? "opacity-100" : "opacity-0"}`}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className={`flex h-[86vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-2xl transition-all duration-200 ${
          visible ? "scale-100 opacity-100" : "scale-95 opacity-0"
        }`}
      >
        {/* topo */}
        <div className="flex items-center justify-between px-6 py-4">
          <h2 className="text-xl font-semibold text-ink">Configurações</h2>
          <button onClick={close} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink">
            <X size={18} />
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* barra lateral de categorias */}
          <div className="flex w-56 shrink-0 flex-col px-3">
            <div className="mb-2 flex items-center gap-2 rounded-lg bg-surface px-3 py-2">
              <Search size={15} className="text-muted" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Pesquisar"
                className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
              />
            </div>
            <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
              {CATS.map((c) => (
                <button
                  key={c.key}
                  onClick={() => { setCat(c.key); setConnView(null); setIntegView(null); }}
                  className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm ${
                    cat === c.key ? "bg-hover font-medium text-ink" : "text-muted hover:bg-hover hover:text-ink"
                  }`}
                >
                  <span className="shrink-0">{c.icon}</span>
                  {c.label}
                </button>
              ))}
            </div>
            {user?.role === "admin" && (
              <div className="flex h-14 shrink-0 items-center border-t border-border">
                <button
                  onClick={() => { close(); router.push("/admin"); }}
                  className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted hover:bg-hover hover:text-ink"
                >
                  <UserCog size={16} /> Administrador
                </button>
              </div>
            )}
          </div>

          {/* coluna de conteúdo: área rolável + rodapé (a barra lateral vai até o fim) */}
          <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-w-0 flex-1 overflow-y-auto px-6 pb-4">
            {searchResults ? (
              <SearchResults
                results={searchResults}
                onPick={(c, view) => {
                  setCat(c); setQ("");
                  setConnView(c === "connections" || c === "interface" ? view ?? null : null);
                }}
              />
            ) : (
            <>
            {cat === "general" && <GeneralTab profile={profile} set={set} />}
            {cat === "shortcuts" && <ShortcutsTab profile={profile} set={set} />}
            {cat === "account" && <AccountTab user={user} profile={profile} set={set} />}
            {cat === "data" && (
              <DataTab
                fileRef={fileRef}
                onArchived={() => setShowArchived(true)}
              />
            )}
            {cat === "connections" && (
              connView === "apis" ? (
                <ApisPanel status={status} reloadSecrets={reloadSecrets} onBack={() => setConnView(null)} />
              ) : connView === "ollama" ? (
                <OllamaPanel onBack={() => setConnView(null)} onChanged={onConnectionsChanged} />
              ) : connView === "voice" ? (
                <VoicePanel onBack={() => setConnView(null)} onChanged={onConnectionsChanged} />
              ) : (
                <div>
                  <Heading>Conexões</Heading>
                  <CardGrid
                    cards={[
                      { key: "apis", icon: <KeyRound size={22} />, name: "APIs", desc: "Chaves de serviços" },
                      { key: "ollama", icon: <SiOllama size={22} />, name: "Ollama", desc: "Utilize modelos locais" },
                      { key: "voice", icon: <AudioLines size={22} />, name: "Voz Local", desc: "Kokoro / clonagem de voz" },
                    ]}
                    onOpen={setConnView}
                  />
                </div>
              )
            )}
            {cat === "integrations" && (
              integView === "google" ? (
                <GoogleWorkspacePanel onBack={() => setIntegView(null)} />
              ) : integView === "tuya" ? (
                <TuyaPanel onBack={() => setIntegView(null)} />
              ) : integView === "whatsapp" ? (
                <WhatsAppPanel onBack={() => setIntegView(null)} />
              ) : (
                <div>
                  <Heading>Integrações</Heading>
                  <div className="grid grid-cols-3 gap-3 pt-1">
                    <button
                      onClick={() => setIntegView("google")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <SiGoogle size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">Google Workspace</span>
                      <span className="text-xs leading-4 text-muted">Ecosistema Google</span>
                    </button>
                    <button
                      onClick={() => setIntegView("tuya")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <Home size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">Tuya Smart Home</span>
                      <span className="text-xs leading-4 text-muted">Casa Inteligente</span>
                    </button>
                    <button
                      onClick={() => setIntegView("whatsapp")}
                      className="group flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center transition-all duration-150 hover:border-accent/40 hover:bg-hover"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
                        <SiWhatsapp size={22} />
                      </span>
                      <span className="text-sm font-medium text-ink">WhatsApp</span>
                      <span className="text-xs leading-4 text-muted">IA atendendo seus números</span>
                    </button>
                    {[
                      { name: "Discord", icon: <SiDiscord size={22} /> },
                      { name: "Slack", icon: <Blocks size={22} /> },
                      { name: "Telegram", icon: <SiTelegram size={22} /> },
                      { name: "Notion", icon: <SiNotion size={22} /> },
                      { name: "GitHub", icon: <SiGithub size={22} /> },
                      { name: "Google Drive", icon: <SiGoogledrive size={22} /> },
                      { name: "Trello", icon: <SiTrello size={22} /> },
                    ].map((it) => (
                      <div key={it.name} className="flex flex-col items-center gap-2.5 rounded-2xl border border-border bg-surface px-4 py-7 text-center opacity-60">
                        <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface2 text-muted">{it.icon}</span>
                        <span className="text-sm font-medium text-ink">{it.name}</span>
                        <span className="text-[10px] text-muted">Em breve</span>
                      </div>
                    ))}
                  </div>
                </div>
              )
            )}
            {cat === "audio" && (
              <div>
                <Heading>Áudio (TTS / STT)</Heading>
                <SecretField label="Chave do provedor de voz" name="voice" configured={status?.voice ?? false} hint="Endpoint compatível com OpenAI (VOICE_BASE_URL). Use OpenAI ou um servidor local." onSaved={reloadSecrets} />
              </div>
            )}
            {cat === "about" && <AboutTab />}
            {cat === "interface" && <InterfaceTab profile={profile} set={set} view={connView} setView={setConnView} />}
            {cat === "personalization" && <PersonalizationTab profile={profile} set={set} />}
            </>
            )}
          </div>
            {/* rodapé — sob a coluna de conteúdo; mesma altura do divisor do admin */}
            <div className="flex h-14 items-center justify-end gap-3 border-t border-border px-6">
              {saveErr && <span className="text-xs text-red-400">{saveErr}</span>}
              {savedFlash && <span className="text-xs text-green-400">Salvo ✓</span>}
              <button onClick={saveProfile} className="rounded-full bg-accent px-6 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
                Salvar
              </button>
            </div>
          </div>
        </div>
      </div>

      {showArchived && <ArchivedModal onChanged={() => {}} onClose={() => setShowArchived(false)} />}
    </div>
  );
}

/* ------------------------------ busca profunda ---------------------------- */
function SearchResults({ results, onPick }: { results: { label: string; cat: Cat; view?: string }[]; onPick: (c: Cat, view?: string) => void }) {
  return (
    <div className="pt-1">
      <Heading>Resultados da busca</Heading>
      {results.length === 0 ? (
        <p className="text-sm text-muted">Nenhuma configuração encontrada.</p>
      ) : (
        <div className="space-y-1.5">
          {results.map((s) => (
            <button
              key={`${s.cat}:${s.label}`}
              onClick={() => onPick(s.cat, s.view)}
              className="flex w-full items-center justify-between gap-3 rounded-xl border border-border bg-surface px-4 py-2.5 text-left transition-colors hover:bg-hover"
            >
              <span className="truncate text-sm text-ink">{s.label}</span>
              <span className="shrink-0 rounded-full bg-surface2 px-2.5 py-0.5 text-xs text-muted">{catLabel(s.cat)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* -------------------------------- Interface ------------------------------- */
// limite do título gerado por IA (espelha MAX_TITLE_CHARS no backend)
const MAX_CHAT_TITLE = 60;

function InterfaceTab({
  profile,
  set,
  view,
  setView,
}: {
  profile: Record<string, any>;
  set: (k: string, v: any) => void;
  view: string | null;
  setView: (v: string | null) => void;
}) {
  if (view === "sidebar") return <SidebarSettings profile={profile} set={set} onBack={() => setView(null)} />;
  return (
    <div>
      <Heading>Interface</Heading>
      <CardGrid
        cards={[{ key: "sidebar", icon: <PanelsTopLeft size={22} />, name: "Barra Lateral", desc: "Aparência e navegação" }]}
        onOpen={setView}
      />
    </div>
  );
}

function SidebarSettings({ profile, set, onBack }: { profile: Record<string, any>; set: (k: string, v: any) => void; onBack: () => void }) {
  const iface: Record<string, any> = profile.interface ?? {};
  const setIface = (k: string, v: any) => set("interface", { ...iface, [k]: v });
  const [cfgOpen, setCfgOpen] = useState(false);
  const [models, setModels] = useState<Model[]>([]);
  useEffect(() => { api.get<Model[]>("/settings/models").then(setModels).catch(() => {}); }, []);
  const autoTitle = !!iface.auto_title;
  return (
    <DetailView title="Barra Lateral" onBack={onBack}>
      <div className="rounded-xl border border-border bg-surface px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-ink">Gerar título de novos chats</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setCfgOpen((v) => !v)}
              title="Configurar"
              className={`rounded-lg p-1.5 transition-colors ${cfgOpen ? "bg-accent/15 text-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
            >
              <Settings size={16} />
            </button>
            <Toggle on={autoTitle} onClick={() => setIface("auto_title", !autoTitle)} />
          </div>
        </div>
        {cfgOpen && (
          <div className="mt-3 space-y-4 border-t border-border pt-3">
            {(iface.title_model || iface.title_prompt) && (
              <div className="flex justify-end">
                <LinkBtn onClick={() => set("interface", { ...iface, title_model: "", title_prompt: "" })}>Redefinir para o padrão</LinkBtn>
              </div>
            )}
            <div className="text-sm">
              <span className="text-ink-soft">Modelo</span>
              <div className="mt-1">
                <ModelField
                  models={models}
                  value={iface.title_model ?? ""}
                  onChange={(v) => setIface("title_model", v)}
                  placeholder="Modelo do chat (padrão)"
                />
              </div>
              <span className="mt-1 block text-xs text-muted">Use um modelo barato. Vazio = modelo do chat.</span>
            </div>
            <label className="block text-sm">
              <span className="text-ink-soft">System prompt</span>
              <textarea
                value={iface.title_prompt ?? ""}
                onChange={(e) => setIface("title_prompt", e.target.value)}
                rows={4}
                maxLength={2000}
                placeholder="Vazio = usa o prompt padrão."
                className="mt-1 w-full resize-y rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent"
              />
              <span className="mt-1 block text-xs text-muted">O título é limitado a {MAX_CHAT_TITLE} caracteres (já informado à IA). Vazio = padrão.</span>
            </label>
          </div>
        )}
      </div>
    </DetailView>
  );
}

/* ---------------------------------- Sobre --------------------------------- */
interface AboutInfo { version: string; latest_version: string | null; update_available: boolean; repo_url: string | null }

function AboutTab() {
  const [info, setInfo] = useState<AboutInfo | null>(null);
  useEffect(() => { api.get<AboutInfo>("/settings/about").then(setInfo).catch(() => {}); }, []);
  const repoUrl = info?.repo_url || "#"; // placeholder até o repositório ser configurado
  return (
    <div>
      <Heading>Sobre</Heading>
      <p className="text-base font-semibold text-ink">AI Workspace</p>
      <p className="mt-1 text-sm text-ink-soft">
        Versão: {info?.version ?? "—"}
        {info?.update_available && info.latest_version && (
          <span className="ml-2 rounded-full bg-accent/15 px-2 py-0.5 text-xs font-medium text-accent-hover">
            {info.latest_version} disponível
          </span>
        )}
      </p>
      <p className="mt-5 text-xs text-muted">Copyright (c) 2026 AI Workspace. All rights reserved.</p>
      <a
        href={repoUrl}
        target={repoUrl === "#" ? undefined : "_blank"}
        rel="noreferrer"
        className="mt-1 inline-block text-xs text-accent-hover transition-colors hover:underline"
      >
        Github Repo
      </a>
      <p className="mt-1 text-xs text-muted">Criado por Victor Alves</p>
    </div>
  );
}

/* --------------------------------- tabs ----------------------------------- */
function GeneralTab({ profile, set }: { profile: Record<string, any>; set: (k: string, v: any) => void }) {
  return (
    <div>
      <Heading>Configurações da WebUI</Heading>
      <Row label="Tema">
        <select
          value={profile.theme ?? "system"}
          onChange={(e) => set("theme", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none"
        >
          <option value="system">Sistema</option>
          <option value="dark">Escuro</option>
          <option value="light">Claro</option>
        </select>
      </Row>
      <Row label="Idioma">
        <select
          value={profile.language ?? "pt-BR"}
          onChange={(e) => set("language", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none"
        >
          <option value="pt-BR">Portuguese (Brazil)</option>
          <option value="en">English</option>
        </select>
      </Row>
      <Row label="Notificações">
        <Toggle on={!!profile.notifications} onClick={() => set("notifications", !profile.notifications)} />
      </Row>
    </div>
  );
}

function PersonalizationTab({ profile, set }: { profile: Record<string, any>; set: (k: string, v: any) => void }) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  return (
    <div>
      <Heading>Prompt do Sistema</Heading>
      <textarea
        rows={6}
        value={profile.system_prompt ?? ""}
        onChange={(e) => set("system_prompt", e.target.value)}
        placeholder="Insira o prompt do sistema aqui"
        className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
      />

      <Heading>Aviso de uso alto</Heading>
      <Row label="Avisar quando uma resposta passar de (tokens)" sub="0 = desligado. Marca a mensagem com um alerta; não bloqueia.">
        <input
          type="number"
          min={0}
          step={1000}
          value={profile.token_warn ?? ""}
          onChange={(e) => set("token_warn", e.target.value === "" ? 0 : Number(e.target.value))}
          placeholder="0"
          className="w-28 rounded-lg border border-border bg-surface px-3 py-1.5 text-right text-sm text-ink outline-none focus:border-accent"
        />
      </Row>

      <Row label="Parâmetros Avançados">
        <LinkBtn onClick={() => setShowAdvanced((v) => !v)}>{showAdvanced ? "Ocultar" : "Mostrar"}</LinkBtn>
      </Row>
      {showAdvanced && (
        <>
          <p className="text-xs text-muted">Parâmetros padrão (JSON) aplicados a novos chats — ex.: temperature, top_p.</p>
          <textarea
            rows={4}
            value={profile.default_params_str ?? ""}
            onChange={(e) => set("default_params_str", e.target.value)}
            placeholder='{ "temperature": 0.7 }'
            className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent placeholder:text-muted"
          />
        </>
      )}
    </div>
  );
}

function AccountTab({ user, profile, set }: { user: User | null; profile: Record<string, any>; set: (k: string, v: any) => void }) {
  const [showPass, setShowPass] = useState(false);
  const [showKeys, setShowKeys] = useState(false);
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [avatarErr, setAvatarErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const name = profile.name ?? "";
  const avatar = profile.avatar ?? "";

  async function pickAvatar(e: React.ChangeEvent<HTMLInputElement>) {
    setAvatarErr(null);
    const file = e.target.files?.[0];
    e.target.value = ""; // permite re-selecionar o mesmo arquivo
    if (!file) return;
    try {
      set("avatar", await fileToAvatarDataUrl(file));
    } catch (err) {
      setAvatarErr(err instanceof Error ? err.message : "Falha ao carregar imagem");
    }
  }

  async function changePassword() {
    setMsg(null);
    try {
      await api.post("/auth/change-password", { current_password: cur, new_password: nw });
      setMsg("Senha alterada ✓"); setCur(""); setNw("");
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Falha ao alterar senha");
    }
  }

  return (
    <div>
      <Heading>Sua conta</Heading>
      <p className="mb-3 text-xs text-muted">Gerencie as informações da sua conta.</p>

      <div className="flex gap-4">
        <div className="flex shrink-0 flex-col items-center">
          <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={pickAvatar} />
          <button
            onClick={() => fileRef.current?.click()}
            title="Enviar uma foto"
            className="group relative h-16 w-16 overflow-hidden rounded-full bg-surface2"
          >
            {avatar ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={avatar} alt="" className="h-full w-full object-cover" />
            ) : (
              <span className="flex h-full w-full items-center justify-center text-lg text-ink">
                {(name || user?.email || "?")[0]?.toUpperCase()}
              </span>
            )}
            <span className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
              <CircleUserRound size={20} className="text-white" />
            </span>
          </button>
          {avatar && (
            <button onClick={() => set("avatar", "")} className="mt-1 w-16 text-center text-[11px] text-muted transition-colors hover:text-ink">
              Remover
            </button>
          )}
          {avatarErr && <p className="mt-1 max-w-16 text-[11px] text-red-400">{avatarErr}</p>}
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-xs text-muted">Nome</p>
          <input
            value={name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Seu nome"
            className="w-full bg-transparent text-lg font-medium text-ink outline-none placeholder:text-muted"
          />
          <p className="pt-1 text-xs text-muted">Sobre você</p>
          <textarea
            rows={2}
            value={profile.about ?? ""}
            onChange={(e) => set("about", e.target.value)}
            placeholder="Fale sobre você e seus interesses"
            className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
          />
        </div>
      </div>

      <Row label="Gênero">
        <select
          value={profile.gender ?? ""}
          onChange={(e) => set("gender", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none"
        >
          <option value="">Prefiro não dizer</option>
          <option value="Masculino">Masculino</option>
          <option value="Feminino">Feminino</option>
          <option value="Outro">Outro</option>
        </select>
      </Row>
      <Row label="Data de nascimento">
        <input
          type="date"
          value={profile.birthdate ?? ""}
          onChange={(e) => set("birthdate", e.target.value)}
          className="rounded-lg bg-surface px-3 py-1.5 text-sm text-ink outline-none [color-scheme:dark]"
        />
      </Row>
      <Row label="Webhook de notificação">
        <input
          value={profile.webhook_url ?? ""}
          onChange={(e) => set("webhook_url", e.target.value)}
          placeholder="Insira a URL do seu webhook"
          className="w-64 rounded-lg bg-surface px-3 py-1.5 text-right text-sm text-ink outline-none placeholder:text-muted"
        />
      </Row>

      <Row label="Alterar Senha">
        <LinkBtn onClick={() => setShowPass((v) => !v)}>{showPass ? "Ocultar" : "Mostrar"}</LinkBtn>
      </Row>
      {showPass && (
        <div className="space-y-2 pb-2">
          <input type="password" value={cur} onChange={(e) => setCur(e.target.value)} placeholder="Senha atual" className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          <input type="password" value={nw} onChange={(e) => setNw(e.target.value)} placeholder="Nova senha (mín. 8)" className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          <div className="flex items-center gap-3">
            <button onClick={changePassword} disabled={!cur || nw.length < 8} className="rounded-lg bg-accent px-4 py-1.5 text-sm text-ink disabled:opacity-50">Alterar senha</button>
            {msg && <span className="text-xs text-muted">{msg}</span>}
          </div>
        </div>
      )}

      <Row label="Chaves API">
        <LinkBtn onClick={() => setShowKeys((v) => !v)}>{showKeys ? "Ocultar" : "Mostrar"}</LinkBtn>
      </Row>
      {showKeys && (
        <p className="pb-2 text-xs text-muted">
          As chaves de provedores (OpenRouter, busca, voz) ficam em <span className="text-ink-soft">Conexões</span> e <span className="text-ink-soft">Áudio</span>. Tokens de acesso à API deste app: em breve.
        </p>
      )}
    </div>
  );
}

/* -------------------------------- Atalhos --------------------------------- */
function ShortcutsTab({ profile, set }: { profile: Record<string, any>; set: (k: string, v: any) => void }) {
  const map: ShortcutMap = profile.shortcuts ?? {};
  const [recording, setRecording] = useState<string | null>(null);

  const setBinding = (id: string, patch: Partial<ShortcutBinding>) => {
    const cur = map[id] ?? {};
    set("shortcuts", { ...map, [id]: { keys: cur.keys ?? "", enabled: cur.enabled ?? true, ...patch } });
  };
  const resetOne = (id: string) => { const next = { ...map }; delete next[id]; set("shortcuts", next); };

  // captura da combinação enquanto "gravando" — exige um modificador; Esc cancela.
  useEffect(() => {
    if (!recording) return;
    const id = recording;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") { e.preventDefault(); setRecording(null); return; }
      const combo = eventToCombo(e);
      if (!combo) return; // só modificador pressionado ainda
      e.preventDefault(); e.stopPropagation();
      if (!comboHasModifier(combo)) return; // precisa de Ctrl/⌘/Alt
      setBinding(id, { keys: combo });
      setRecording(null);
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [recording, map]); // eslint-disable-line react-hooks/exhaustive-deps

  // conta combos p/ sinalizar conflito (mesma tecla em 2 ações ativas)
  const counts = new Map<string, number>();
  for (const a of SHORTCUTS) { const b = resolveBinding(map, a); if (b.enabled) counts.set(b.keys, (counts.get(b.keys) ?? 0) + 1); }

  return (
    <div>
      <div className="flex items-center justify-between">
        <Heading>Atalhos de teclado</Heading>
        <LinkBtn onClick={() => set("shortcuts", {})}>Restaurar padrões</LinkBtn>
      </div>
      {SHORTCUT_GROUPS.map((g) => (
        <div key={g.title} className="mb-4">
          <p className="mb-1.5 mt-1 text-xs font-medium uppercase tracking-wider text-muted">{g.title}</p>
          <div className="space-y-1">
            {g.actions.map((a) => {
              const b = resolveBinding(map, a);
              const isRec = recording === a.id;
              const conflict = b.enabled && (counts.get(b.keys) ?? 0) > 1;
              const custom = !!map[a.id]?.keys;
              return (
                <div key={a.id} className="flex items-center gap-2.5 rounded-lg border border-border bg-surface px-3 py-2">
                  <span className={`flex-1 truncate text-sm ${b.enabled ? "text-ink" : "text-muted line-through"}`}>{a.label}</span>
                  {conflict && <span className="shrink-0 text-[11px] text-amber-400">em conflito</span>}
                  <button
                    onClick={() => setRecording(isRec ? null : a.id)}
                    className={`min-w-[96px] shrink-0 rounded-md border px-2 py-1 text-center text-xs transition-colors ${isRec ? "animate-pulse border-accent bg-accent/10 text-accent-hover" : "border-border text-ink-soft hover:bg-hover"}`}
                  >
                    {isRec ? "Pressione…" : (
                      <span className="flex items-center justify-center gap-1">
                        {prettyCombo(b.keys).map((k, i) => (
                          <kbd key={i} className="rounded bg-surface2 px-1.5 py-0.5 font-mono text-[10px] leading-4 text-ink-soft">{k}</kbd>
                        ))}
                      </span>
                    )}
                  </button>
                  <button onClick={() => resetOne(a.id)} title="Voltar ao padrão" disabled={!custom} className="shrink-0 rounded-md p-1 text-muted transition-colors hover:text-ink disabled:opacity-30">
                    <RotateCcw size={14} />
                  </button>
                  <Toggle on={b.enabled} onClick={() => setBinding(a.id, { enabled: !b.enabled })} />
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <p className="text-xs text-muted">
        Clique no atalho e pressione a combinação — precisa incluir <span className="text-ink-soft">Ctrl/⌘</span> ou <span className="text-ink-soft">Alt</span>. Esc cancela.
        Não disparam enquanto você digita (a menos que use Ctrl/⌘/Alt). Lembre de salvar.
      </p>
    </div>
  );
}

function DataTab({ fileRef, onArchived }: { fileRef: React.RefObject<HTMLInputElement>; onArchived: () => void }) {
  const [busy, setBusy] = useState(false);
  const confirm = useConfirm();
  // Memória da IA: liga/desliga geral (o controle de dados da memória mora aqui;
  // os detalhes por escopo ficam em Espaço → Memória).
  const [mem, setMem] = useState<{ enabled?: boolean } | null>(null);
  useEffect(() => { api.get<{ enabled?: boolean }>("/memory/settings").then(setMem).catch(() => {}); }, []);
  const memOn = mem?.enabled === true;
  async function toggleMem() {
    const next = { ...(mem ?? {}), enabled: !memOn };
    setMem(next);
    await api.put("/memory/settings", next).catch(() => {});
  }

  async function exportChats() {
    const pw = (window.prompt("Senha para CIFRAR o export (deixe vazio para exportar sem criptografia):") ?? "").trim();
    const res = await api.post<{ encrypted: boolean; blob?: string; items?: unknown[] }>(
      "/chats/bulk/export",
      pw ? { password: pw } : {},
    );
    const content = res.encrypted ? (res.blob ?? "") : JSON.stringify(res.items ?? [], null, 2);
    const blob = new Blob([content], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = res.encrypted ? "chats.enc.json" : "chats.json"; a.click();
    URL.revokeObjectURL(url);
  }

  async function importChats(file: File) {
    setBusy(true);
    try {
      const text = await file.text();
      let body: any;
      try {
        const parsed = JSON.parse(text);
        if (parsed && parsed.aw_enc === 1) {
          const pw = (window.prompt("Este arquivo está cifrado. Digite a senha:") ?? "").trim();
          if (!pw) { setBusy(false); return; }
          body = { blob: text, password: pw };
        } else {
          body = { items: Array.isArray(parsed) ? parsed : (parsed.items ?? [parsed]) };
        }
      } catch { alert("Arquivo inválido."); return; }
      await api.post("/chats/bulk/import", body);
      alert("Chats importados. Recarregue para vê-los.");
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao importar.");
    } finally {
      setBusy(false);
    }
  }

  async function archiveAll() {
    if (!(await confirm({ title: "Arquivar TODOS os chats?", confirmLabel: "Arquivar" }))) return;
    await api.post("/chats/bulk/archive-all");
    alert("Todos os chats foram arquivados. Recarregue a página.");
  }
  async function deleteAll() {
    const ok = await confirm({
      title: "Excluir TODOS os chats?",
      body: "Esta ação é irreversível.",
      confirmLabel: "Excluir tudo",
      danger: true,
    });
    if (!ok) return;
    await api.post("/chats/bulk/delete-all");
    alert("Todos os chats foram excluídos. Recarregue a página.");
  }

  return (
    <div>
      <input
        ref={fileRef}
        type="file"
        accept="application/json"
        className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) importChats(f); e.target.value = ""; }}
      />
      <Heading>Chats</Heading>
      <Row label="Importar Chats"><LinkBtn onClick={() => fileRef.current?.click()}>{busy ? "…" : "Importar"}</LinkBtn></Row>
      <Row label="Exportar Chats"><LinkBtn onClick={exportChats}>Exportar</LinkBtn></Row>
      <Row label="Chats Arquivados"><LinkBtn onClick={onArchived}>Gerenciar</LinkBtn></Row>
      <Row label="Chats compartilhados"><LinkBtn onClick={() => alert("Em breve")}>Gerenciar</LinkBtn></Row>
      <Row label="Arquivar Todos os Chats"><LinkBtn onClick={archiveAll}>Arquivar tudo</LinkBtn></Row>
      <Row label="Excluir Todos os Chats">
        <button onClick={deleteAll} className="shrink-0 text-sm text-red-400 hover:text-red-300">Excluir tudo</button>
      </Row>

      <Heading>Memória da IA</Heading>
      <Row label="Memória" sub="Deixe a IA lembrar de fatos entre conversas. Gerencie o conteúdo em Espaço → Memória.">
        <Toggle on={memOn} onClick={toggleMem} />
      </Row>

      <Heading>Arquivos</Heading>
      <Row label="Gerenciar arquivos"><LinkBtn onClick={() => alert("Em breve")}>Gerenciar</LinkBtn></Row>
    </div>
  );
}

/* ------------------------------- Conexões --------------------------------- */
function ApisPanel({ status, reloadSecrets, onBack }: { status: SecretStatus | null; reloadSecrets: () => void; onBack: () => void }) {
  return (
    <DetailView title="APIs" onBack={onBack}>
      <Heading>Modelos e voz</Heading>
      <SecretField label="Chave do OpenRouter" name="openrouter" configured={status?.openrouter ?? false} hint="Provedor de modelos (obrigatória para conversar)." onSaved={reloadSecrets} />
      <SecretField label="Chave do provedor de voz" name="voice" configured={status?.voice ?? false} hint="TTS/STT — endpoint compatível com OpenAI (também em Áudio)." onSaved={reloadSecrets} />
      <Heading>Pesquisa na web</Heading>
      <SecretField label="Chave Tavily" name="tavily" configured={status?.tavily ?? false} hint="tavily.com — ferramenta Pesquisa na Web / Deep Search." onSaved={reloadSecrets} />
      <SecretField label="Chave Brave Search" name="brave" configured={status?.brave ?? false} hint="brave.com/search/api" onSaved={reloadSecrets} />
      <Heading>Finanças</Heading>
      <SecretField label="Chave Finnhub" name="finnhub" configured={status?.finnhub ?? false} hint="finnhub.io — ferramenta Cotação (Ações)." onSaved={reloadSecrets} />
      <SecretField label="Chave Alpha Vantage" name="alphavantage" configured={status?.alphavantage ?? false} hint="alphavantage.co" onSaved={reloadSecrets} />
    </DetailView>
  );
}

