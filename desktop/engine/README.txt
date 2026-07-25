AI Workspace - motor embarcado (Windows, sem Docker)
=====================================================

Esta pasta e' autocontida: traz o Postgres, o Python e o backend do AI Workspace.
Nada e' instalado no sistema.

COMO RODAR
----------
1. Clique com o botao direito em "Start-AIWorkspace.ps1" > "Executar com o PowerShell".
   (ou, num terminal PowerShell nesta pasta:  ./Start-AIWorkspace.ps1)
2. Na primeira vez ele inicializa o banco e aplica as migracoes (demora um pouco).
3. Quando aparecer "API em http://127.0.0.1:8000", esta no ar.
   Abra http://127.0.0.1:8000/docs no navegador para conferir.
4. Ctrl+C encerra (o Postgres e' desligado junto).

Se o Windows bloquear o script, rode uma vez:
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

O QUE FICA ONDE
---------------
Tudo em .\data\ :  banco (pgdata), caches de modelo e o segredo do app.
Apagar .\data\ zera tudo. Backup = copiar .\data\.

OBSERVACOES DESTA ETAPA (1)
---------------------------
- Este e' o "motor" (API). A interface web e o app de bandeja entram nas etapas
  seguintes.
- Na primeira execucao o backend baixa uma vez o modelo de embeddings (precisa de
  internet nesse momento); depois roda offline.
- Recursos que dependem de binarios externos (OCR de imagem, transcricao por FALA
  de video, busca de codigo por ripgrep, backup pg_dump no painel) ficam
  desligados/degradados aqui - entram numa etapa posterior. O nucleo (chat,
  memoria, conhecimento) funciona.
