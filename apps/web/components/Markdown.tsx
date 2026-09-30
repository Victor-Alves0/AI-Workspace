"use client";

import { isValidElement, memo, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { marked, type Token, type Tokens } from "marked";
import remend from "remend";
import hljs from "highlight.js/lib/common";
import { Check, Copy, File, ImageOff } from "lucide-react";
import { API_URL, previewHref } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import SoundChip, { SFX_PROTOCOL, soundPromptFromHref, withSoundLinks } from "./SoundChip";

/*
 * Markdown das mensagens, no formato dos harnesses de referência (opencode
 * `markdown-stream.ts`, streamdown da Vercel):
 *  - o texto vira BLOCOS de topo (`marked.lexer`); cada bloco é um componente
 *    memorizado pelo próprio texto — durante o streaming os blocos que já fecharam
 *    não re-renderizam, só o último muda;
 *  - o último bloco, ainda incompleto, passa pelo `remend` (fecha **negrito**, `código`,
 *    [links] pela metade) em vez de piscar entre formatado e cru;
 *  - bloco de código é um componente PRÓPRIO desde a 1ª linha (cerca aberta já é
 *    código): o botão "Copiar" não é desmontado a cada pedaço que chega, o realce de
 *    sintaxe vale também ao vivo, e a cerca pode trazer o nome do arquivo
 *    (```python title="main.py"```).
 */

interface ElProps {
  className?: string;
  children?: React.ReactNode;
}

// extrai o texto puro de uma árvore React (links, código aninhado em listas)
function textOf(node: React.ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<ElProps>(node)) return textOf(node.props.children);
  return "";
}

// ---------------------------------------------------------------------------
// Bloco de código
// ---------------------------------------------------------------------------

/** Linguagem e título a partir da "info string" da cerca:
 *  ```python · ```python title="main.py" · ```python main.py · ```python:main.py · ```main.py */
export function parseFenceInfo(info: string | undefined): { lang: string; title: string } {
  const s = (info ?? "").trim();
  if (!s) return { lang: "", title: "" };
  const titleAttr = /(?:title|file|filename|name)\s*=\s*(?:"([^"]+)"|'([^']+)'|(\S+))/i.exec(s);
  let first = s.split(/\s+/, 1)[0];
  let title = titleAttr ? (titleAttr[1] ?? titleAttr[2] ?? titleAttr[3] ?? "") : "";
  if (first.includes("=")) first = "";
  const colon = first.indexOf(":");
  if (colon > 0) {
    title = title || first.slice(colon + 1);
    first = first.slice(0, colon);
  }
  if (!title) {
    const rest = s.slice(s.split(/\s+/, 1)[0].length).trim();
    if (rest && !rest.includes("=") && !/[{}]/.test(rest)) title = rest;
  }
  // "```main.py" sozinho: é nome de arquivo, a linguagem vem da extensão
  if (!title && /\.[a-z0-9]{1,6}$/i.test(first) && !hljs.getLanguage(first)) {
    title = first;
    first = first.slice(first.lastIndexOf(".") + 1);
  }
  return { lang: first.toLowerCase(), title: title.slice(0, 120) };
}

const LANG_LABEL: Record<string, string> = {
  js: "JavaScript", javascript: "JavaScript", jsx: "JSX", ts: "TypeScript", typescript: "TypeScript",
  tsx: "TSX", py: "Python", python: "Python", sh: "Shell", bash: "Bash", zsh: "Shell", shell: "Shell",
  ps1: "PowerShell", powershell: "PowerShell", json: "JSON", yaml: "YAML", yml: "YAML", html: "HTML",
  css: "CSS", scss: "SCSS", sql: "SQL", rust: "Rust", rs: "Rust", go: "Go", java: "Java", kotlin: "Kotlin",
  c: "C", cpp: "C++", "c++": "C++", cs: "C#", csharp: "C#", php: "PHP", ruby: "Ruby", rb: "Ruby",
  swift: "Swift", md: "Markdown", markdown: "Markdown", xml: "XML", dockerfile: "Dockerfile",
  toml: "TOML", ini: "INI", diff: "Diff", lua: "Lua", r: "R", text: "Texto", txt: "Texto", plaintext: "Texto",
};

const AUTO_MAX = 6000;        // autodetecção só em blocos pequenos (percorre várias gramáticas)
const HIGHLIGHT_MAX = 60000;  // acima disso, texto monoespaçado (realce travaria a UI)
const AUTO_SUBSET = ["python", "javascript", "typescript", "bash", "json", "css", "xml", "sql", "java", "cpp", "csharp", "go", "rust", "php", "yaml", "ruby", "kotlin", "powershell"];

function highlight(code: string, lang: string, auto: boolean): { html: string; lang: string } | null {
  if (!code || code.length > HIGHLIGHT_MAX) return null;
  try {
    if (lang && hljs.getLanguage(lang)) return { html: hljs.highlight(code, { language: lang, ignoreIllegals: true }).value, lang };
    if (!lang && auto && code.length <= AUTO_MAX) {
      const r = hljs.highlightAuto(code, AUTO_SUBSET);
      if (r.language && r.relevance >= 5) return { html: r.value, lang: r.language };
    }
  } catch {
    /* gramática quebrou: cai no texto puro */
  }
  return null;
}

/** Cabeçalho grudado no topo enquanto o bloco rola: nesse estado os cantos ficam
 *  retos (arredondados, o código passando por trás aparecia nos cantos). */
function useStuck(header: React.RefObject<HTMLDivElement | null>, box: React.RefObject<HTMLDivElement | null>) {
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const h = header.current;
    const b = box.current;
    if (!h || !b) return;
    let el: HTMLElement | null = b.parentElement;
    while (el && !/(auto|scroll)/.test(getComputedStyle(el).overflowY)) el = el.parentElement;
    const alvo: HTMLElement | Window = el ?? window;
    let raf = 0;
    const medir = () => {
      raf = 0;
      setStuck(h.getBoundingClientRect().top - b.getBoundingClientRect().top > 1);
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(medir); };
    alvo.addEventListener("scroll", onScroll, { passive: true });
    medir();
    return () => { alvo.removeEventListener("scroll", onScroll); if (raf) cancelAnimationFrame(raf); };
  }, [header, box]);
  return stuck;
}

export function CodeBlock({ code, info, streaming = false }: { code: string; info?: string; streaming?: boolean }) {
  const [copied, setCopied] = useState(false);
  const header = useRef<HTMLDivElement | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  const stuck = useStuck(header, box);
  const { lang, title } = useMemo(() => parseFenceInfo(info), [info]);
  const body = code.replace(/\n$/, "");
  // ao vivo: realça só com a linguagem declarada (autodetecção muda de ideia a cada pedaço)
  const hl = useMemo(() => highlight(body, lang, !streaming), [body, lang, streaming]);
  const langShown = hl?.lang || lang;
  const label = title || LANG_LABEL[langShown] || langShown || "código";
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  async function copy() {
    try {
      await copyText(body);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1600);
    } catch {
      /* sem permissão de área de transferência */
    }
  }

  return (
    <div ref={box} className="relative my-3 rounded-xl border border-border">
      <div
        ref={header}
        className={`md-code-sticky-header sticky z-20 flex items-center justify-between gap-2 border-b border-border bg-surface px-3 py-1.5 ${stuck ? "rounded-none shadow-md" : "rounded-t-xl"}`}
      >
        <span className={`min-w-0 truncate text-[11px] text-muted ${title ? "font-mono" : "font-mono uppercase tracking-wider"}`} title={title && langShown ? `${title} · ${langShown}` : undefined}>
          {label}
        </span>
        <button
          type="button"
          onClick={copy}
          className="flex shrink-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] text-muted transition-colors hover:bg-hover hover:text-ink"
        >
          {copied ? <Check size={12} className="text-green-400" /> : <Copy size={12} />}
          {copied ? "Copiado" : "Copiar"}
        </button>
      </div>
      <pre className="rounded-b-xl">
        {hl
          ? <code className={`hljs language-${hl.lang}`} dangerouslySetInnerHTML={{ __html: hl.html }} />
          : <code className="hljs">{body}</code>}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Imagens
// ---------------------------------------------------------------------------

/** Imagem inline com fallback: se a URL falhar (ex.: token expirado/adulterado),
 *  em vez do quadro quebrado feio mostra um chip clicável com o nome do arquivo,
 *  que abre a imagem em nova aba. */
const _VIDEO_ALT_RE = /\.(mp4|webm|mov|m4v|ogv|mkv)\s*$/i;

function MdImage({ src, alt, streaming = false }: { src: string; alt: string; streaming?: boolean }) {
  // O `done` troca a URL provisória (copiada pelo modelo) por outra reassinada pelo
  // servidor. Guardar apenas um booleano fazia o erro da URL antiga sobreviver à
  // troca: o player bom nunca era tentado e ficava preso no chip de mídia quebrada.
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const broken = !!src && failedSrc === src;
  // A rota curta que chega nos tokens ainda não tem a assinatura final. Não dispara
  // um request que inevitavelmente daria 403 nem marca a mídia como quebrada; o evento
  // `done` troca `src` pelo endereço assinado e então o player é carregado normalmente.
  if (streaming && src.includes("/knowledge/docs/")) {
    return (
      <span
        className="my-2 inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs text-muted"
        title="A mídia será carregada quando a resposta terminar"
      >
        <File size={14} className="shrink-0" />
        <span className="max-w-[240px] truncate">{alt || "Preparando mídia…"}</span>
      </span>
    );
  }
  // vídeos da Base de Conhecimento chegam como ![clip.mp4](url): o nome (alt)
  // termina numa extensão de vídeo → renderiza um player em vez de <img>.
  if (src && !broken && _VIDEO_ALT_RE.test(alt || "")) {
    return (
      <video
        key={src}
        src={src}
        controls
        preload="metadata"
        onError={() => setFailedSrc(src)}
        className="my-2 max-h-96 max-w-full rounded-xl border border-border"
      />
    );
  }
  if (broken || !src) {
    return (
      <a
        href={src || undefined}
        target="_blank"
        rel="noreferrer noopener"
        className="my-2 inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs text-muted no-underline transition-colors hover:text-ink"
        title={alt || "imagem"}
      >
        <ImageOff size={14} className="shrink-0" />
        <span className="max-w-[240px] truncate">{alt || "imagem"}</span>
      </a>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setFailedSrc(src)}
      className="my-2 max-h-96 max-w-full rounded-xl border border-border object-contain"
    />
  );
}

// ---------------------------------------------------------------------------
// Blocos
// ---------------------------------------------------------------------------

type Block =
  | { kind: "md"; raw: string; live: boolean }
  | { kind: "code"; raw: string; code: string; info: string; complete: boolean };

/** Cerca ainda aberta no fim do bloco de código (o fechamento não chegou). */
function fenceOpen(raw: string): boolean {
  const m = /^[ \t]{0,3}(`{3,}|~{3,})/.exec(raw);
  if (!m) return false;
  const last = raw.trimEnd().split("\n").at(-1)?.trim() ?? "";
  if (raw.trimEnd().split("\n").length < 2) return true;
  return !new RegExp(`^[\\t ]{0,3}${m[1][0] === "`" ? "`" : "~"}{${m[1].length},}[\\t ]*$`).test(last);
}

/** Definições de link por referência ([x]: url) valem para o texto todo: não dá para
 *  cortar em blocos sem perder os links — vai inteiro. */
function hasRefDefs(text: string) {
  return text.includes("]:") && /^[ \t]{0,3}\[[^\]]+\]:[ \t]*\S/m.test(text);
}

export function splitBlocks(text: string, live: boolean): Block[] {
  if (!text) return [];
  if (hasRefDefs(text)) return [{ kind: "md", raw: text, live }];
  let tokens: Token[];
  try {
    tokens = marked.lexer(text, { gfm: true });
  } catch {
    return [{ kind: "md", raw: text, live }];
  }
  const out: Block[] = [];
  let pending = "";   // texto markdown acumulado (espaços entre blocos vão junto)
  const flushMd = (isLive: boolean) => {
    if (pending.trim()) out.push({ kind: "md", raw: pending, live: isLive });
    pending = "";
  };
  const lastIdx = tokens.findLastIndex((t) => t.type !== "space");
  tokens.forEach((t, i) => {
    if (t.type === "code" && (t as Tokens.Code).codeBlockStyle !== "indented") {
      flushMd(false);
      const c = t as Tokens.Code;
      const firstLine = c.raw.slice(0, c.raw.indexOf("\n") < 0 ? c.raw.length : c.raw.indexOf("\n"));
      const info = firstLine.replace(/^[ \t]{0,3}(`{3,}|~{3,})/, "").trim();
      const aberto = live && i === lastIdx && fenceOpen(c.raw);
      out.push({ kind: "code", raw: c.raw, code: c.text, info, complete: !aberto });
      return;
    }
    // cada bloco de topo é um componente: fecha o anterior quando começa um novo
    if (t.type !== "space" && pending.trim()) flushMd(false);
    pending += t.raw;
  });
  flushMd(live);
  return out;
}

const MD_COMPONENTS = (live: boolean) => ({
  pre: ({ children }: { children?: React.ReactNode }) => {
    // código aninhado (ex.: dentro de uma lista) ainda passa pelo parser do bloco
    const el = Array.isArray(children) ? children[0] : children;
    const cls = isValidElement<ElProps>(el) ? el.props.className ?? "" : "";
    const lang = /language-([\w+#.-]+)/.exec(cls)?.[1] ?? "";
    return <CodeBlock code={textOf(el)} info={lang} streaming={live} />;
  },
  table: ({ children }: { children?: React.ReactNode }) => (
    <div className="md-table-wrap">
      <table>{children}</table>
    </div>
  ),
  a: ({ children, href }: { children?: React.ReactNode; href?: string }) => {
    const som = soundPromptFromHref(href);
    if (som) return <SoundChip prompt={som} label={textOf(children) || som} />;
    const h = previewHref(href);
    return <a href={h} target="_blank" rel="noreferrer noopener">{children}</a>;
  },
  img: ({ src, alt }: { src?: string | Blob; alt?: string }) => {
    const url = typeof src === "string" && src.startsWith("/") ? `${API_URL}${src}` : src;
    return <MdImage src={typeof url === "string" ? url : ""} alt={alt ?? ""} streaming={live} />;
  },
});
const COMPONENTS_LIVE = MD_COMPONENTS(true);
const COMPONENTS_DONE = MD_COMPONENTS(false);
const REMARK = [remarkGfm];
const urlTransform = (url: string) => (url.startsWith(SFX_PROTOCOL) ? url : defaultUrlTransform(url));

/** Um bloco markdown. Memorizado pelo texto: blocos fechados não re-renderizam. */
const MdChunk = memo(function MdChunk({ raw, live }: { raw: string; live: boolean }) {
  // efeitos sonoros: `[[som: ...]]` vira um link `sfx:` que o `a` troca pelo botão
  const src = withSoundLinks(live ? remend(raw, { linkMode: "text-only" }) : raw, live);
  return (
    <ReactMarkdown remarkPlugins={REMARK} urlTransform={urlTransform} components={live ? COMPONENTS_LIVE : COMPONENTS_DONE}>
      {src}
    </ReactMarkdown>
  );
});

const CodeChunk = memo(function CodeChunk({ code, info, complete }: { code: string; info: string; complete: boolean }) {
  return <CodeBlock code={code} info={info} streaming={!complete} />;
});

// Acima deste tamanho, parsear a mensagem inteira de uma vez trava a UI ("uma
// Wikipédia escrita no chat"). Renderizamos só um prefixo e o usuário expande.
const CLAMP_LIMIT = 8000;

/** Compat: remove a cerca/crase aberta do fim (usado por quem ainda pinta texto cru). */
export function stabilizeStream(md: string): string {
  return remend(md, { linkMode: "text-only" });
}

/**
 * Markdown das mensagens do assistente: GFM (tabelas, listas de tarefas,
 * links automáticos) + realce de sintaxe. Tipografia via classe `.md`.
 * `fast` = ao vivo (streaming). `clamp` evita travar em mensagens gigantes.
 */
function Markdown({
  content,
  className = "",
  clamp = false,
  fast = false,
}: {
  content: string;
  className?: string;
  clamp?: boolean;
  fast?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  // streaming nunca é truncado (esconderia o mais recente e o botão piscaria)
  const isLong = clamp && !fast && !expanded && content.length > CLAMP_LIMIT;
  const clamped = isLong
    ? (() => {
        const cut = content.lastIndexOf("\n\n", CLAMP_LIMIT);
        return content.slice(0, cut > CLAMP_LIMIT / 2 ? cut : CLAMP_LIMIT);
      })()
    : content;
  const blocks = useMemo(() => splitBlocks(clamped, fast), [clamped, fast]);
  return (
    <div className={`md ${className}`}>
      {blocks.map((b, i) =>
        b.kind === "code"
          ? <CodeChunk key={i} code={b.code} info={b.info} complete={b.complete} />
          : <MdChunk key={i} raw={b.raw} live={b.live} />,
      )}
      {isLong && (
        <button
          onClick={() => setExpanded(true)}
          className="mt-2 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-ink-soft transition-colors hover:bg-hover hover:text-ink"
        >
          Mostrar mensagem completa · {content.length.toLocaleString("pt-BR")} caracteres
        </button>
      )}
    </div>
  );
}

export default memo(Markdown);
