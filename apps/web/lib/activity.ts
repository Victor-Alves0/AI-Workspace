/** Frases do "o que a IA está fazendo agora", montadas a partir da ferramenta chamada e
 *  dos argumentos dela ("Pesquisando “preço do café”", "Editando src/app.ts",
 *  "Delegando “comparar preços” para Pesquisador"). Só "Pensando" e "Respondendo" são fixos. */

type Args = Record<string, unknown>;

const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
const cut = (s: string, n = 48) => {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n - 1).trimEnd() + "…" : t;
};
const q = (s: string, n?: number) => `“${cut(s, n)}”`;
const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return cut(url, 40);
  }
};
const base = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() || path;

/** O arquivo de um diff unificado ("+++ b/src/x.ts"), e quantos há. */
function diffFiles(diff: string): string[] {
  return [...diff.matchAll(/^\+\+\+ (?:b\/)?(.+)$/gm)].map((m) => m[1].trim()).filter((f) => f !== "/dev/null");
}

const DOCS = /(^|\/)(readme|changelog|contributing|docs?\/)|\.(md|mdx|rst|txt)$/i;
const TESTS = /(^|\/)(tests?|__tests__|spec)\/|[._-](test|spec)\.[a-z]+$/i;

function writing(path: string, verbo: string) {
  if (DOCS.test(path)) return `${verbo === "Criando" ? "Escrevendo" : verbo} documentação (${base(path)})`;
  if (TESTS.test(path)) return `${verbo} testes (${base(path)})`;
  return `${verbo} ${cut(path, 56)}`;
}

function describeCode(tool: string, a: Args): string | null {
  const action = str(a.action);
  const path = str(a.path);
  switch (tool) {
    case "code.files.browse":
      if (action === "search") return `Procurando ${q(str(a.query) || "no código")} no projeto`;
      if (action === "log") return "Lendo o histórico de commits";
      if (action === "diff") return path ? `Conferindo as mudanças em ${base(path)}` : "Conferindo as mudanças";
      if (action === "list") return path ? `Explorando a pasta ${cut(path, 40)}` : "Explorando os arquivos do projeto";
      return path ? `Lendo ${cut(path, 56)}` : "Lendo arquivos do projeto";
    case "code.files.write": {
      if (action === "push") return "Enviando os commits para o repositório";
      if (action === "delete") return path ? `Apagando ${base(path)}` : "Apagando um arquivo";
      if (action === "patch") {
        const files = Array.isArray(a.files) ? (a.files as string[]) : diffFiles(str(a.diff));
        if (files.length === 1) return writing(files[0], "Editando");
        if (files.length > 1) return `Editando ${files.length} arquivos`;
        return "Editando arquivos do projeto";
      }
      if (!path) return "Editando arquivos do projeto";
      return writing(path, action === "write" ? "Criando" : "Editando");
    }
    case "code.exec.run": {
      const cmd = str(a.command) || str(a.cmd);
      if (/\b(test|pytest|jest|vitest|cargo test|go test|mvn test)\b/.test(cmd)) return `Rodando os testes (${cut(cmd, 36)})`;
      if (/\b(build|compile|tsc|gradlew|mvn package)\b/.test(cmd)) return `Compilando (${cut(cmd, 36)})`;
      if (/\b(install|add|pip|npm i|pnpm i|yarn)\b/.test(cmd)) return `Instalando dependências (${cut(cmd, 36)})`;
      return cmd ? `Rodando ${q(cmd, 44)}` : "Rodando um comando no projeto";
    }
    case "code.exec.jobs":
      return "Acompanhando um comando em segundo plano";
    case "code.preview.serve":
      return action === "stop" ? "Parando o preview" : action === "logs" ? "Lendo os logs do preview" : "Pondo o app no ar";
    case "code.graph.query":
      return a.symbol || a.query ? `Analisando ${q(str(a.symbol) || str(a.query), 40)} no grafo de código` : "Analisando o grafo de código";
    case "code.flow.analyze":
      return "Analisando o fluxo do código";
    case "code.task.manage":
      if (action === "open") return a.title ? `Abrindo a tarefa ${q(str(a.title), 40)}` : "Abrindo uma tarefa isolada";
      if (action === "merge") return "Mesclando a tarefa";
      if (action === "diff") return "Revisando o diff da tarefa";
      return "Organizando as tarefas do projeto";
  }
  return null;
}

function describePath(tool: string, a: Args): string | null {
  const code = describeCode(tool, a);
  if (code) return code;
  const action = str(a.action);
  switch (tool) {
    case "web.search.query":
      return a.query ? `Pesquisando ${q(str(a.query))}` : "Pesquisando na web";
    case "web.page.read":
      return a.url ? `Lendo ${host(str(a.url))}` : "Lendo uma página";
    case "web.browser.use":
      if (action === "goto" && a.url) return `Abrindo ${host(str(a.url))} no navegador`;
      if (action === "click") return a.target ? `Clicando em ${q(str(a.target), 32)}` : "Clicando na página";
      if (action === "type") return "Preenchendo um campo";
      if (action === "screenshot") return "Capturando a tela";
      return "Navegando no navegador";
    case "research.deep.run":
      return a.query || a.topic ? `Pesquisando a fundo ${q(str(a.query) || str(a.topic))}` : "Fazendo uma pesquisa profunda";
    case "media.video.transcribe":
      return a.url ? `Transcrevendo o vídeo de ${host(str(a.url))}` : "Transcrevendo o vídeo";
  }
  return null;
}

/** Ferramentas sem regra própria: a ação e, quando há, o alvo da chamada. */
const GENERIC: Record<string, string> = {
  "utils.time.now": "Conferindo a data e a hora",
  "utils.math.eval": "Fazendo as contas",
  "user.profile.get": "Consultando o seu perfil",
  "github.public.search": "Pesquisando no GitHub",
  "security.exploitdb.search": "Pesquisando no Exploit-DB",
  "security.cve.search": "Consultando CVEs",
  "skills.library.manage": "Organizando as skills",
  "prompts.library.manage": "Organizando os prompts",
  "task.ledger.track": "Atualizando o plano da tarefa",
  "http.session.use": "Fazendo requisições HTTP",
  "diagram.excalidraw.render": "Desenhando o diagrama",
  "chart.render.plot": "Montando o gráfico",
  "finance.quote.get": "Consultando a cotação",
  "automation.monitor.create": "Criando um monitor",
  "automation.reminder.create": "Agendando um lembrete",
  "google.gmail.mailbox": "Verificando o e-mail",
  "google.calendar.events": "Consultando a agenda",
  "smartlife.tuya.devices": "Controlando a casa",
  "github.repo.manage": "Trabalhando no GitHub",
  "notion.workspace.manage": "Trabalhando no Notion",
  "slack.workspace.manage": "Trabalhando no Slack",
  "messaging.chat.manage": "Cuidando das mensagens",
  "higgsfield.media.generate": "Gerando mídia no Higgsfield",
  "civitai.media.use": "Usando o Civitai",
  "elevenlabs.audio.generate": "Gerando áudio",
  "vercel.projects.manage": "Trabalhando na Vercel",
  "spotify.music.search": "Buscando no Spotify",
  "investigation.graph.manage": "Atualizando o grafo de investigação",
  "remote.terminal.run": "Rodando no terminal remoto",
};
const TARGET_KEYS = ["query", "q", "title", "symbol", "ticker", "name", "command"] as const;

function generic(path: string, a: Args): string | null {
  const frase = GENERIC[path];
  if (!frase) return null;
  const alvo = TARGET_KEYS.map((k) => str(a[k])).find(Boolean);
  return alvo ? `${frase}: ${q(alvo, 40)}` : frase;
}

/** Nome de ferramenta legível para o que não tem frase própria. */
function humanize(tool: string) {
  return tool.replace(/__/g, ".").replace(/[._]/g, " ").trim();
}

/** Frase para uma chamada de ferramenta da IA principal. */
export function describeToolCall(name: string, data: unknown, extra?: { agent?: string; team?: string; done?: number; size?: number }): string {
  const a = (data && typeof data === "object" ? data : {}) as Args;
  switch (name) {
    case "delegate": {
      const quem = extra?.agent || str(a.name) || (str(a.agent) !== "new" ? str(a.agent) : "") || "um agente";
      const task = str(a.task);
      return task ? `Delegando ${q(task, 44)} para ${quem}` : `Delegando uma tarefa para ${quem}`;
    }
    case "delegate_team": {
      const equipe = extra?.team || str(a.team_name) || "a equipe";
      const nome = equipe === "a equipe" ? equipe : `a equipe ${equipe}`;
      if (extra?.size) return `Coordenando ${nome}: ${extra.done ?? 0} de ${extra.size} concluídos`;
      const n = Array.isArray(a.members) ? a.members.length : 0;
      return n ? `Montando ${nome} com ${n} agentes` : `Coordenando ${nome}`;
    }
    case "execute_tool":
      return describeStep(str(a.path) || "ferramenta", "", (a.params as Args) ?? {});
    case "search_tools":
      return a.query ? `Procurando uma ferramenta para ${q(str(a.query), 36)}` : "Procurando a ferramenta certa";
    case "get_tool_schema":
      return "Consultando como usar uma ferramenta";
    case "run_code":
      return "Executando código";
    case "generate_image":
      return a.prompt ? `Gerando a imagem ${q(str(a.prompt), 40)}` : "Gerando uma imagem";
    case "search_knowledge":
      return a.query ? `Consultando a base de conhecimento: ${q(str(a.query), 36)}` : "Consultando a base de conhecimento";
    case "brain":
      return str(a.action) === "write" || str(a.action) === "create" ? "Anotando no segundo cérebro" : "Consultando o segundo cérebro";
    case "view_skill":
      return a.slug || a.name ? `Lendo a skill ${str(a.slug) || str(a.name)}` : "Lendo uma skill";
    case "propose_skill":
      return "Propondo uma nova skill";
  }
  return describeStep(name, "", a);
}

/** Frase para um passo (ferramenta em notação com pontos + detalhe curto), usada também
 *  pelos subagentes, que só mandam `tool` e `detail`. */
export function describeStep(tool: string, detail = "", args: Args = {}): string {
  const path = tool.replace(/__/g, ".");
  if (path === "delegate_team") {
    const equipe = str(args.team_name) || detail;
    const n = typeof args.members === "number" ? args.members : 0;
    return `Montando ${equipe ? `a equipe ${equipe}` : "uma equipe"}${n ? ` com ${n} agentes` : ""}`;
  }
  if (path === "delegate") {
    const task = str(args.task) || detail;
    const quem = str(args.name);
    return `Delegando ${task ? q(task, 44) : "uma tarefa"}${quem ? ` para ${quem}` : ""}`;
  }
  // sem os argumentos, o detalhe (o 1º texto da chamada) faz as vezes do principal
  const a: Args = Object.keys(args).length
    ? args
    : { query: detail, url: detail, path: detail, command: detail, prompt: detail, title: detail };
  const frase = describePath(path, a) ?? generic(path, Object.keys(args).length ? args : { query: detail });
  if (frase) return frase;
  const nome = humanize(path);
  return detail ? `Usando ${nome}: ${cut(detail, 40)}` : `Usando ${nome}`;
}
