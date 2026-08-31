"use client";

import { isValidElement, memo, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Check, Copy, ImageOff } from "lucide-react";
import { API_URL, previewHref } from "@/lib/api";
import { copyText } from "@/lib/clipboard";

interface ElProps {
  className?: string;
  children?: React.ReactNode;
}

// extrai o texto puro de uma árvore React (p/ copiar código já realçado)
function textOf(node: React.ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<ElProps>(node)) return textOf(node.props.children);
  return "";
}

function CodeBlock({ children }: { children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const codeEl = Array.isArray(children) ? children[0] : children;
  let lang = "";
  if (isValidElement<ElProps>(codeEl)) {
    const m = /language-([\w+-]+)/.exec(codeEl.props.className ?? "");
    if (m) lang = m[1];
  }

  async function copy() {
    try {
      await copyText(textOf(codeEl).replace(/\n$/, ""));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border">
      <div className="flex items-center justify-between border-b border-border bg-surface px-3 py-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted">
          {lang || "código"}
        </span>
        <button
          onClick={copy}
          className="flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] text-muted transition-colors hover:bg-hover hover:text-ink"
        >
          {copied ? <Check size={12} className="text-green-400" /> : <Copy size={12} />}
          {copied ? "Copiado" : "Copiar"}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

/** Imagem inline com fallback: se a URL falhar (ex.: token expirado/adulterado),
 *  em vez do quadro quebrado feio mostra um chip clicável com o nome do arquivo,
 *  que abre a imagem em nova aba. */
const _VIDEO_ALT_RE = /\.(mp4|webm|mov|m4v|ogv|mkv)\s*$/i;

function MdImage({ src, alt }: { src: string; alt: string }) {
  const [broken, setBroken] = useState(false);
  // vídeos da Base de Conhecimento chegam como ![clip.mp4](url): o nome (alt)
  // termina numa extensão de vídeo → renderiza um player em vez de <img>.
  if (src && !broken && _VIDEO_ALT_RE.test(alt || "")) {
    return (
      <video
        src={src}
        controls
        preload="metadata"
        onError={() => setBroken(true)}
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
      onError={() => setBroken(true)}
      className="my-2 max-h-96 max-w-full rounded-xl border border-border object-contain"
    />
  );
}

// Acima deste tamanho, realçar/parsear a mensagem inteira de uma vez trava a UI
// ("uma Wikipédia escrita no chat"). Renderizamos só um prefixo e deixamos o
// usuário expandir o resto sob demanda.
const CLAMP_LIMIT = 8000;
// Durante o stream, os primeiros parágrafos já estão fechados e não precisam ser
// parseados novamente. Só a cauda muda a cada flush; deixá-la pequena mantém a UI
// responsiva mesmo quando a resposta cresce por vários milhares de caracteres.
const STREAMING_TAIL_LIMIT = 2400;

/**
 * Estabiliza markdown PARCIAL durante o streaming: enquanto os tokens chegam, uma
 * crase/cerca de código aberta faz TODO o texto seguinte "virar código" até a de
 * fechamento chegar — e volta no próximo flush. Esse flip-flop é o "piscar" que o
 * usuário via. Fechamos/removemos os delimitadores abertos só p/ renderizar (o texto
 * real não muda): cerca ``` ímpar → fecha; crase inline solta na última linha → remove.
 */
function stabilizeStream(md: string): string {
  const fences = (md.match(/^ {0,3}```/gm) || []).length;
  if (fences % 2 === 1) return `${md}\n\`\`\``; // fecha o bloco de código aberto
  const nl = md.lastIndexOf("\n");
  const lastLine = md.slice(nl + 1);
  // NÃO mexer numa linha de cerca (``` de fechamento tem 3 crases, nº ímpar) — só
  // numa crase INLINE órfã (ex.: "... o `patternScan" ainda sem a de fechamento).
  if (!/^ {0,3}```/.test(lastLine) && ((lastLine.match(/`/g) || []).length) % 2 === 1) {
    return md.slice(0, md.lastIndexOf("`"));
  }
  return md;
}

/** Divide o texto em uma parte Markdown estável e uma cauda ainda mutável.
 * A divisão é só em quebra de parágrafo e nunca dentro de uma cerca de código,
 * preservando os blocos Markdown usuais enquanto só a cauda é reprocessada. */
function splitStreamingMarkdown(md: string): { stable: string; tail: string } {
  if (md.length <= STREAMING_TAIL_LIMIT) return { stable: "", tail: md };
  const beforeTail = md.slice(0, md.length - STREAMING_TAIL_LIMIT);
  const boundary = beforeTail.lastIndexOf("\n\n");
  if (boundary < 0) return { stable: "", tail: md };
  const stable = md.slice(0, boundary + 2);
  // Uma cerca aberta precisa permanecer na mesma árvore que seu fechamento. Em
  // respostas com um único bloco enorme, o clamp ainda limita o trabalho a 8k.
  if ((stable.match(/^ {0,3}```/gm) || []).length % 2 !== 0) return { stable: "", tail: md };
  return { stable, tail: md.slice(boundary + 2) };
}

function MarkdownRenderer({ content, fast }: { content: string; fast: boolean }) {
  const shown = fast ? stabilizeStream(content) : content;
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      // detect: realça também blocos SEM tag de linguagem (```` sem "python") —
      // o hljs adivinha entre as linguagens comuns. Com tag, usa a declarada.
      rehypePlugins={fast ? [] : [[rehypeHighlight, { detect: true }]]}
      components={{
        pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        table: ({ children }) => (
          <div className="md-table-wrap">
            <table>{children}</table>
          </div>
        ),
        a: ({ children, href }) => {
          const h = previewHref(href);
          return <a href={h} target="_blank" rel="noreferrer noopener">{children}</a>;
        },
        img: ({ src, alt }) => {
          const url = typeof src === "string" && src.startsWith("/") ? `${API_URL}${src}` : src;
          return <MdImage src={typeof url === "string" ? url : ""} alt={alt ?? ""} />;
        },
      }}
    >
      {shown}
    </ReactMarkdown>
  );
}

const StableMarkdownRenderer = memo(MarkdownRenderer);

/**
 * Markdown das mensagens do assistente: GFM (tabelas, listas de tarefas,
 * links automáticos) + realce de sintaxe. Tipografia via classe `.md`.
 * `clamp` evita travar em mensagens gigantes: mostra um prefixo + "Mostrar tudo".
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
  // `fast`: pula o realce de sintaxe (rehypeHighlight) — usado no STREAMING, onde
  // o conteúdo é re-parseado a cada atualização e o realce da mensagem inteira
  // travava a UI em respostas longas. O realce volta na mensagem já persistida.
  fast?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const isLong = clamp && !expanded && content.length > CLAMP_LIMIT;
  // corta num limite de parágrafo p/ não deixar uma cerca de código aberta
  const clamped = isLong
    ? (() => {
        const cut = content.lastIndexOf("\n\n", CLAMP_LIMIT);
        return content.slice(0, cut > CLAMP_LIMIT / 2 ? cut : CLAMP_LIMIT);
      })()
    : content;
  const streamingParts = useMemo(
    () => fast ? splitStreamingMarkdown(clamped) : { stable: "", tail: clamped },
    [clamped, fast],
  );
  return (
    <div className={`md ${className}`}>
      {streamingParts.stable && <StableMarkdownRenderer content={streamingParts.stable} fast />}
      <MarkdownRenderer content={streamingParts.tail} fast={fast} />
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
