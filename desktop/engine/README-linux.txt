AI Workspace - motor embarcado (Linux, sem Docker)
==================================================

Esta pasta e' autocontida: traz o Postgres, o Python e o backend do AI Workspace.
Nada e' instalado no sistema (so' precisa de glibc 2.35+, i.e. Ubuntu 22.04 ou mais novo).

COMO RODAR
----------
    ./start-aiworkspace.sh

1. Na primeira vez ele prepara o banco e aplica as migracoes.
2. Quando a interface subir, o navegador abre sozinho em http://localhost:41414
   (se a porta estiver ocupada, usa a proxima livre ate' 41433).
3. Ctrl+C encerra tudo (banco e API).

Opcoes:  --data-dir DIR   onde ficam banco/caches/segredo (padrao: ./data)
         --no-browser     nao abre o navegador
         --port N         porta preferida

NO TERMINAL
-----------
    ./aiw            chat no terminal (os mesmos chats do app)
    ./aiw -c         continua o ultimo chat;  ./aiw --help  para o resto

O QUE FICA ONDE
---------------
Tudo em ./data/ (ou --data-dir): banco (pgdata), caches de modelo, segredo do app e,
se houver, a atualizacao leve do codigo (appcode/). Apagar = zerar. Backup = copiar.

OBSERVACOES
-----------
- Nao rode como root: o Postgres se recusa.
- Na primeira execucao o backend baixa uma vez o modelo de embeddings (precisa de
  internet nesse momento); depois roda offline.
- OCR (tesseract), transcricao por fala (ffmpeg) e busca de codigo (ripgrep) usam os
  binarios do sistema, se estiverem instalados.
