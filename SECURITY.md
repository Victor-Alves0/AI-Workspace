# Política de Segurança

## Reportar uma vulnerabilidade

Se você encontrou uma vulnerabilidade de segurança no AI Workspace, **não abra uma issue
pública**. Em vez disso:

- Use o **[Report a vulnerability](https://github.com/Victor-Alves0/AI-Workspace/security/advisories/new)**
  do GitHub (aba **Security → Advisories**) para um relato privado, **ou**
- Entre em contato de forma privada com o mantenteor do repositório.

Inclua, se possível:

- Uma descrição do problema e do impacto.
- Passos para reproduzir (ou uma prova de conceito).
- Versão/commit afetado e o ambiente.

Você receberá um retorno assim que o relato for avaliado. Por favor, dê um tempo razoável para
correção antes de qualquer divulgação pública.

## Escopo

Este é um projeto **self-hosted**: quem o instala é responsável por proteger a própria
infraestrutura (rede, backups, `APP_SECRET`, senha do Postgres, HTTPS). Recomendações de
endurecimento estão em [docs/security.md](docs/security.md).

Pontos especialmente sensíveis a ter em mente:

- **`ALLOW_CODE_MODE`** executa código gerado pelo modelo num sandbox — leia as ressalvas em
  [docs/security.md](docs/security.md#execução-de-ferramentas-sandbox) antes de expor a
  usuários não confiáveis.
- **Segredos** dependem do `APP_SECRET`; trate-o como material criptográfico.
