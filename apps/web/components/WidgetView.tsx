"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Code2, Copy, Download, MoreHorizontal } from "lucide-react";
import { AnchoredMenu, MenuItem } from "./ui";
import { toast } from "./Toaster";
import { copyText } from "@/lib/clipboard";
import { triggerDownload } from "@/lib/download";
import { tr } from "@/lib/i18n";

/** Visual inline (tool `visual.widget.show`): SVG ou fragmento HTML escrito pela IA. */
export type WidgetSpec = { mode: "svg" | "html"; code: string; title?: string };

// Paleta do tema escuro exposta ao código da IA como CSS variables (o mesmo contrato
// descrito na tool, no servidor). Hex fixo: o iframe não enxerga as variáveis do app.
const BG = "#141417";
const BASE: Record<string, string> = {
  bg: BG, surface: "#1a1a1e", surface2: "#232329", border: "#3a3a42",
  ink: "#ececf0", "ink-soft": "#c9c9d1", muted: "#9d9da8", accent: "#7c6bff",
};
// [fill (fundo suave), stroke (contorno), text (texto sobre o fill)]
const RAMPS: Record<string, [string, string, string]> = {
  purple: ["#2d2650", "#8d7eff", "#c9c1ff"],
  teal: ["#0f3d36", "#2bb39a", "#8fe3d2"],
  coral: ["#5a2a18", "#e0703f", "#ffb59a"],
  amber: ["#44340f", "#e0a82e", "#ffd88a"],
  blue: ["#132e4d", "#4c8ee8", "#a9cbff"],
  green: ["#173d22", "#4cb36a", "#a6e5b5"],
  red: ["#4a1c1f", "#e0565b", "#ffaeb0"],
  gray: ["#26262c", "#6b6b78", "#c9c9d1"],
};
const FONT = 'Inter, "Segoe UI", ui-sans-serif, system-ui, sans-serif';

function themeVars(): string {
  const out = Object.entries(BASE).map(([k, v]) => `--${k}:${v}`);
  for (const [k, [fill, stroke, text]] of Object.entries(RAMPS)) {
    out.push(`--fill-${k}:${fill}`, `--stroke-${k}:${stroke}`, `--text-${k}:${text}`);
  }
  return out.join(";");
}

// estilos de base do iframe; o `fill` no <svg> só vale p/ quem não declarou o próprio
const BASE_CSS =
  `:root{${themeVars()};color-scheme:dark}` +
  `html,body{margin:0;padding:0;background:transparent;color:var(--ink);font:14px/1.5 ${FONT};overflow:hidden}` +
  "#root{padding:2px}" +
  `#root>svg{display:block;width:100%;height:auto;max-width:100%;fill:var(--ink);font-family:${FONT};font-size:13px}` +
  "button,input,select,textarea{font:inherit;color:inherit}" +
  "a{color:var(--accent)}";

// sem rede além dos CDNs de scripts (mesmos que o servidor anuncia ao modelo)
const CSP =
  "default-src 'none'; script-src 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; " +
  "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'";

// Ponte com o app: reporta a altura do conteúdo e expõe sendPrompt(texto), que envia
// uma mensagem no chat — só com gesto do usuário (clique), nunca sozinho.
const BRIDGE = [
  "(function(){",
  "var post=function(m){parent.postMessage(Object.assign({aiwWidget:1},m),'*')};",
  "window.sendPrompt=function(t){",
  "  var ua=navigator.userActivation; if(ua&&!ua.isActive)return;",
  "  post({prompt:String(t==null?'':t).slice(0,4000)});",
  "};",
  "var last=0;function size(){var r=document.getElementById('root');if(!r)return;",
  "  var h=Math.ceil(r.getBoundingClientRect().height);if(h!==last){last=h;post({height:h})}}",
  "document.addEventListener('DOMContentLoaded',function(){",
  "  size();try{new ResizeObserver(size).observe(document.getElementById('root'))}catch(e){}",
  "});window.addEventListener('load',size);",
  "})();",
].join("\n");

function buildDoc(code: string, bridge: boolean): string {
  return [
    '<!doctype html><html><head><meta charset="utf-8" />',
    `<meta http-equiv="Content-Security-Policy" content="${CSP}" />`,
    `<style>${BASE_CSS}</style>`,
    bridge ? `<script>${BRIDGE}</` + "script>" : "",
    `</head><body><div id="root">${code}</div></body></html>`,
  ].join("\n");
}

function fileSlug(title?: string): string {
  return (title || "visual").normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/[^\w-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "visual";
}

/** SVG autônomo para exportar: variáveis do tema embutidas, fundo escuro e tamanho
 *  explícito (sem isso o <img> do PNG não tem dimensão intrínseca). */
function standaloneSvg(code: string): { svg: string; w: number; h: number } | null {
  // parser HTML de propósito: tolera &nbsp; e xmlns ausente, e põe o <svg> no namespace certo
  const el = new DOMParser().parseFromString(code, "text/html").body.querySelector("svg");
  if (!el) return null;
  const num = (v: string | null) => {
    const n = parseFloat(v ?? "");
    return Number.isFinite(n) && n > 0 ? n : 0;
  };
  const vb = (el.getAttribute("viewBox") ?? "").trim().split(/[\s,]+/).map(Number);
  const hasVb = vb.length === 4 && vb.every(Number.isFinite) && vb[2] > 0 && vb[3] > 0;
  const w = hasVb ? vb[2] : num(el.getAttribute("width")) || 680;
  const h = hasVb ? vb[3] : num(el.getAttribute("height")) || 400;
  if (!hasVb) el.setAttribute("viewBox", `0 0 ${w} ${h}`);
  el.setAttribute("width", String(w));
  el.setAttribute("height", String(h));
  const ns = "http://www.w3.org/2000/svg";
  const style = document.createElementNS(ns, "style");
  style.textContent = `svg{${themeVars()};fill:var(--ink);font-family:${FONT};font-size:13px}`;
  const bg = document.createElementNS(ns, "rect");
  bg.setAttribute("x", String(hasVb ? vb[0] : 0));
  bg.setAttribute("y", String(hasVb ? vb[1] : 0));
  bg.setAttribute("width", String(w));
  bg.setAttribute("height", String(h));
  bg.setAttribute("fill", BG);
  el.insertBefore(bg, el.firstChild);
  el.insertBefore(style, el.firstChild);
  return { svg: new XMLSerializer().serializeToString(el), w, h };
}

async function svgToPng(svg: string, w: number, h: number): Promise<Blob> {
  const img = new Image();
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  await img.decode();
  const scale = Math.min(3, Math.max(1, 2000 / Math.max(w, h)), 4096 / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas");
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("png"))), "image/png"));
}

/** Visual inline gerado pela IA, desenhado num iframe isolado (sem acesso ao app:
 *  sandbox sem allow-same-origin). O menu "…" copia/baixa como imagem (SVG) ou o
 *  código (HTML). */
export default function WidgetView({ spec }: { spec: WidgetSpec }) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const menuBtn = useRef<HTMLButtonElement>(null);
  const lastPrompt = useRef(0);
  const [height, setHeight] = useState(spec.mode === "svg" ? 240 : 160);
  const [menu, setMenu] = useState(false);
  const srcDoc = useMemo(() => buildDoc(spec.code, true), [spec.code]);
  const name = fileSlug(spec.title);

  useEffect(() => {
    function onMsg(e: MessageEvent) {
      if (!frameRef.current || e.source !== frameRef.current.contentWindow) return;
      const d = e.data as { aiwWidget?: number; height?: unknown; prompt?: unknown } | null;
      if (!d || d.aiwWidget !== 1) return;
      if (typeof d.height === "number" && Number.isFinite(d.height)) {
        setHeight(Math.max(40, Math.min(3000, Math.ceil(d.height))));
      }
      if (typeof d.prompt === "string" && d.prompt.trim()) {
        // um clique = uma mensagem: segura cliques repetidos/loops do script
        const now = Date.now();
        if (now - lastPrompt.current < 1500) return;
        lastPrompt.current = now;
        window.dispatchEvent(new CustomEvent("aiw:send-prompt", { detail: { text: d.prompt.trim() } }));
      }
    }
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  function exported() {
    const out = standaloneSvg(spec.code);
    if (!out) toast(tr("Não foi possível exportar este visual."));
    return out;
  }

  async function copyImage() {
    setMenu(false);
    const out = exported();
    if (!out) return;
    if (!window.isSecureContext || !navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
      toast(tr("Copiar imagem exige HTTPS. Use Baixar como PNG."));
      return;
    }
    try {
      // a Promise (e não o Blob pronto) mantém o gesto do usuário válido no Safari
      await navigator.clipboard.write([new ClipboardItem({ "image/png": svgToPng(out.svg, out.w, out.h) })]);
      toast(tr("Imagem copiada."), "success");
    } catch {
      toast(tr("Não foi possível copiar a imagem."));
    }
  }

  function downloadSvg() {
    setMenu(false);
    const out = exported();
    if (out) triggerDownload(new Blob([out.svg], { type: "image/svg+xml" }), `${name}.svg`);
  }

  async function downloadPng() {
    setMenu(false);
    const out = exported();
    if (!out) return;
    try {
      triggerDownload(await svgToPng(out.svg, out.w, out.h), `${name}.png`);
    } catch {
      toast(tr("Não foi possível exportar este visual."));
    }
  }

  async function copyCode() {
    setMenu(false);
    if (await copyText(spec.code)) toast(tr("Código copiado."), "success");
    else toast(tr("Não foi possível copiar."));
  }

  function downloadHtml() {
    setMenu(false);
    triggerDownload(new Blob([buildDoc(spec.code, false)], { type: "text/html" }), `${name}.html`);
  }

  return (
    <div className="group relative my-3">
      <iframe
        ref={frameRef}
        title={spec.title || tr("Visual")}
        srcDoc={srcDoc}
        sandbox="allow-scripts"
        className="block w-full border-0 bg-transparent"
        style={{ height }}
      />
      <button
        ref={menuBtn}
        onClick={() => setMenu((v) => !v)}
        title={tr("Opções do visual")}
        className={`touch-reveal absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-lg border border-border bg-surface text-muted transition-opacity hover:text-ink ${
          menu ? "opacity-100" : "opacity-0 group-hover:opacity-100"
        }`}
      >
        <MoreHorizontal size={15} />
      </button>
      {menu && (
        <AnchoredMenu anchorRef={menuBtn} onClose={() => setMenu(false)}>
          {spec.mode === "svg" ? (
            <>
              <MenuItem icon={<Copy size={15} />} onClick={copyImage}>{tr("Copiar como imagem")}</MenuItem>
              <MenuItem icon={<Download size={15} />} onClick={downloadSvg}>{tr("Baixar como SVG")}</MenuItem>
              <MenuItem icon={<Download size={15} />} onClick={downloadPng}>{tr("Baixar como PNG")}</MenuItem>
            </>
          ) : (
            <>
              <MenuItem icon={<Code2 size={15} />} onClick={copyCode}>{tr("Copiar código")}</MenuItem>
              <MenuItem icon={<Download size={15} />} onClick={downloadHtml}>{tr("Baixar como HTML")}</MenuItem>
            </>
          )}
        </AnchoredMenu>
      )}
    </div>
  );
}
