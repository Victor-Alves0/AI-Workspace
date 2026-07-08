"use client";

import { useMemo, useState } from "react";
import { Maximize2, Minimize2, PenTool } from "lucide-react";

// versões fixadas (mesmas validadas no script original do Victor)
const EXCALIDRAW_V = "0.18.1";
const MERMAID_V = "2.2.2";
const REACT_V = "19.0.0";

/** Monta o documento isolado do iframe. O canvas (Excalidraw + mermaid-to-excalidraw)
 *  é carregado dentro do iframe a partir do esm.sh; o app não incha o bundle e o
 *  código de terceiros fica confinado. O `mermaid` chega já sem cercas de código
 *  (limpo pelo backend). Sem template literals/backticks no JS interno de propósito.
 *
 *  Tema escuro: NÃO forçamos viewBackgroundColor — o tema "dark" do Excalidraw já
 *  aplica um filtro de inversão no canvas (fundo branco padrão vira escuro e os
 *  traços pretos do Mermaid viram claros). Forçar um fundo escuro aqui fazia o
 *  filtro invertê-lo p/ cinza-claro e "apagar" o desenho. */
function buildSrcDoc(mermaid: string, title: string, height: number): string {
  const payload = JSON.stringify({ mermaid, title: title || "Diagrama", height })
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026");
  return [
    '<!doctype html><html><head><meta charset="utf-8" />',
    '<link rel="stylesheet" href="https://esm.sh/@excalidraw/excalidraw@' + EXCALIDRAW_V + '/dist/prod/index.css" />',
    "<script>window.EXCALIDRAW_ASSET_PATH = \"https://esm.sh/@excalidraw/excalidraw@" + EXCALIDRAW_V + "/dist/prod/\";</" + "script>",
    '<script type="importmap">{ "imports": {',
    '"react": "https://esm.sh/react@' + REACT_V + '",',
    '"react/jsx-runtime": "https://esm.sh/react@' + REACT_V + '/jsx-runtime",',
    '"react-dom": "https://esm.sh/react-dom@' + REACT_V + '",',
    '"react-dom/client": "https://esm.sh/react-dom@' + REACT_V + '/client"',
    "} }</" + "script>",
    "<style>",
    "html,body,#app{margin:0;padding:0;width:100%;height:" + height + "px;overflow:hidden;background:#121212;}",
    "#app{font-family:Inter,ui-sans-serif,system-ui,sans-serif;}",
    ".msg{display:flex;align-items:center;justify-content:center;height:100%;color:#9d9da8;font-size:13px;padding:16px;text-align:center;}",
    ".err{position:absolute;left:10px;bottom:10px;max-width:70%;z-index:9;padding:8px 10px;border-radius:10px;background:#fef2f2;border:1px solid #fecaca;color:#991b1b;font-size:12px;display:none;}",
    // remove o gatilho da biblioteca (menu de 3 botões à direita no layout compacto)
    ".sidebar-trigger,.default-sidebar-trigger{display:none!important;}",
    // remove o botão de ajuda flutuante ('?')
    ".help-icon{display:none!important;}",
    // desafoga os botões de desfazer/refazer p/ o canto inferior DIREITO, soltos
    ".undo-redo-buttons{position:absolute!important;right:14px!important;bottom:14px!important;left:auto!important;top:auto!important;}",
    "</style></head><body>",
    '<div id="app" class="msg">Carregando canvas…</div>',
    '<div id="err" class="err"></div>',
    '<script type="module">',
    "const CONFIG = " + payload + ";",
    "const fatal = (m) => { const a=document.getElementById('app'); a.className='msg'; a.textContent='Não foi possível carregar o Excalidraw: '+m; };",
    "async function main(){",
    "  const RM = await import('react'); const React = RM.default || RM;",
    "  const RD = await import('react-dom/client'); const createRoot = RD.createRoot || (RD.default && RD.default.createRoot);",
    "  const exc = await import('https://esm.sh/@excalidraw/excalidraw@" + EXCALIDRAW_V + "/dist/prod/index.js?external=react,react-dom');",
    "  const mmd = await import('https://esm.sh/@excalidraw/mermaid-to-excalidraw@" + MERMAID_V + "');",
    "  const h = React.createElement;",
    "  const App = () => {",
    "    const [api, setApi] = React.useState(null);",
    "    React.useEffect(() => {",
    "      if(!api) return;",
    "      mmd.parseMermaidToExcalidraw(CONFIG.mermaid, { fontSize: 20 }).then((parsed) => {",
    "        const els = exc.convertToExcalidrawElements(parsed.elements || []);",
    "        api.updateScene({ elements: els });",
    "        const files = parsed.files ? Object.values(parsed.files) : [];",
    "        if(files.length && api.addFiles) api.addFiles(files);",
    "        setTimeout(() => { try { api.scrollToContent(els, { fitToViewport: true, animate: true, duration: 300 }); } catch(e){} }, 100);",
    "      }).catch((err) => { const e=document.getElementById('err'); e.textContent='Mermaid inválido: '+(err && err.message ? err.message : err); e.style.display='block'; });",
    "    }, [api]);",
    // menu customizado: só Exportar imagem, Salvar em arquivo e Tema. Sem 'Abrir',
    // 'Limpar canvas', 'Ajuda' nem a seção de links do Excalidraw.
    "    const M = exc.MainMenu; const DI = M.DefaultItems;",
    "    const menu = h(M, {}, h(DI.SaveAsImage), h(DI.Export), h(DI.ToggleTheme));",
    "    return h(exc.Excalidraw, {",
    "      excalidrawAPI: setApi,",
    "      theme: 'dark', langCode: 'pt-BR', name: CONFIG.title,",
    // remove a UI do canto superior direito (botão da biblioteca)
    "      renderTopRightUI: () => null,",
    "      UIOptions: { canvasActions: { loadScene: false, clearCanvas: false, saveAsImage: true, export: { saveFileToDisk: true }, toggleTheme: true } }",
    "    }, menu);",
    "  };",
    "  const app = document.getElementById('app'); app.className=''; app.textContent='';",
    "  createRoot(app).render(h(App));",
    "}",
    "main().catch((e) => fatal(e && e.message ? e.message : String(e)));",
    "</" + "script></body></html>",
  ].join("\n");
}

/** Canvas Excalidraw (tema escuro) renderizado a partir de um flowchart Mermaid
 *  produzido pela IA (ferramenta `diagram.excalidraw.render`). Isolado em iframe.
 *  Altura > 500px de propósito: abaixo disso o Excalidraw entra no layout mobile
 *  (barra inferior compacta) — no desktop o menu fica no topo-esquerdo. */
export default function ExcalidrawCanvas({ mermaid, title }: { mermaid: string; title?: string }) {
  const [expanded, setExpanded] = useState(false);
  const height = expanded ? 860 : 600;
  const srcDoc = useMemo(() => buildSrcDoc(mermaid, title ?? "Diagrama", height), [mermaid, title, height]);

  return (
    <div className="my-2 overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
        <PenTool size={13} className="text-accent-hover" />
        <span className="truncate text-xs font-medium text-ink">{title || "Diagrama"}</span>
        <span className="ml-auto text-[10px] text-muted">Excalidraw</span>
        <button
          onClick={() => setExpanded((v) => !v)}
          title={expanded ? "Recolher" : "Expandir"}
          className="rounded p-1 text-muted transition-colors hover:bg-hover hover:text-ink"
        >
          {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
        </button>
      </div>
      <iframe
        title={title || "Excalidraw"}
        srcDoc={srcDoc}
        sandbox="allow-scripts allow-same-origin allow-downloads allow-popups"
        className="block w-full border-0"
        style={{ height }}
      />
    </div>
  );
}
