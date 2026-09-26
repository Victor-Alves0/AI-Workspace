import fs from "node:fs";
import path from "node:path";
import Markdown from "@/components/Markdown";
import BackButton from "./BackButton";

/** Casca das páginas jurídicas (/privacy e /terms).
 *
 *  O texto vem de `content/*.md` e NÃO está duplicado em JSX de propósito: o mesmo
 *  arquivo é a URL pública que os consoles de OAuth (Google, Notion, Slack) exigem —
 *  eles precisam de um endereço alcançável na internet, e o blob do GitHub serve
 *  antes mesmo de existir um domínio publicado. Uma segunda cópia em código
 *  divergiria da primeira na primeira revisão.
 *
 *  Componente de SERVIDOR: a leitura acontece no build, então a página sai estática
 *  (o `content/` não precisa existir no runtime da imagem). E é pública — não passa
 *  pelo guard de sessão, senão o Google não conseguiria abrir para verificar. */
export default function LegalPage({ file }: { file: "privacy" | "terms" }) {
  const md = fs.readFileSync(
    path.join(process.cwd(), "content", `${file}.md`),
    "utf-8",
  );
  // o <body> do app não rola (cada tela é uma moldura de 100dvh): a página jurídica
  // precisa da PRÓPRIA área de rolagem, e de um jeito de voltar
  return (
    <div className="h-full overflow-y-auto">
    <div className="sticky top-0 z-10 border-b border-border bg-bg/90 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-center px-3 py-2">
        <BackButton />
      </div>
    </div>
    <main className="mx-auto max-w-3xl px-5 py-10">
      <Markdown content={md} />
      <p className="mt-12 border-t border-border pt-5 text-xs text-muted">
        <a href="/" className="transition-colors hover:text-ink">AI Workspace</a>
        {" · "}
        <a href="/privacy" className="transition-colors hover:text-ink">Privacidade</a>
        {" · "}
        <a href="/terms" className="transition-colors hover:text-ink">Termos</a>
      </p>
    </main>
    </div>
  );
}
