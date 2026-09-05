# AI Workspace — Remote Terminal Agent

Agente que dá ao AI Workspace um terminal **na sua máquina** (VPS, servidor de casa,
workstation), com controle de saída de rede e killswitch.

Um arquivo, só biblioteca padrão do Python 3. Numa VPS crua: copiar, rodar o instalador,
pronto — sem pip, sem venv, sem container.

```bash
scp -r apps/remote-agent root@SEU_IP:/tmp/
ssh root@SEU_IP 'cd /tmp/remote-agent && sudo ./install.sh --san SEU_IP'
```

Para que os comandos saiam **obrigatoriamente** por um proxy, com firewall e killswitch:

```bash
sudo ./install.sh --san SEU_IP --proxy socks5h://127.0.0.1:9050 --force-egress
```

O instalador imprime **endereço, token e certificado** — cole em Configurações →
Integrações → Remote Terminal.

## Arquivos

| Arquivo | O que é |
|---------|---------|
| `aiw_remote_agent.py` | o agente: API HTTPS, execução, jobs e política de saída |
| `install.sh` | usuário dedicado, systemd, TLS, token; `--uninstall` desfaz tudo |

## Opções do instalador

| Opção | Efeito |
|-------|--------|
| `--san <ip\|domínio>` | endereço pelo qual o workspace chega (entra no certificado) |
| `--port <n>` | porta do agente (padrão 8791) |
| `--bind <addr>` | interface de escuta (use `127.0.0.1` atrás de um túnel) |
| `--user <nome>` | usuário que executa os comandos (padrão `aiw-remote`) |
| `--proxy <url>` | proxy de saída dos comandos (`socks5h://…` ou `http://…`) |
| `--force-egress` | firewall por-uid + killswitch (exige `--proxy`) |
| `--env-egress` | só variáveis de ambiente — **sem** killswitch |
| `--grant-sudo` | sudo NOPASSWD ao usuário dos comandos (**incompatível** com `--force-egress`) |
| `--uninstall` | remove serviço, regras de firewall e arquivos |

`--grant-sudo` com `--force-egress` é recusado de propósito: `sudo` vira uid 0 e escapa da
regra que casa pelo uid do usuário dedicado. O killswitch continuaria "ligado" enquanto
qualquer `sudo curl` sairia pelo IP real.

## Comandos úteis na máquina

```bash
aiw_remote_agent.py --config /etc/aiw-remote-agent/config.json --status       # política ativa
aiw_remote_agent.py --config /etc/aiw-remote-agent/config.json --test-egress  # teste de vazamento
aiw_remote_agent.py --config /etc/aiw-remote-agent/config.json --print-token
aiw_remote_agent.py --config /etc/aiw-remote-agent/config.json --new-token    # revoga o anterior
journalctl -u aiw-remote-agent -f
```

## API (token no `Authorization: Bearer`)

| Rota | O que faz |
|------|-----------|
| `GET /v1/health` | vivo? (sem token — para monitoração externa) |
| `GET /v1/ping` | SO, usuário, root?, política de saída ativa |
| `POST /v1/exec` | roda e espera: `{command, cwd, timeout, env, shell}` |
| `POST /v1/jobs` | dispara em segundo plano → `{job_id}` |
| `GET /v1/jobs[/{id}?wait=N]` | lista / consulta (long-poll opcional) |
| `POST /v1/jobs/{id}/kill` | mata o job |
| `GET\|PUT /v1/egress` | lê / aplica a política de saída |
| `POST /v1/egress/test` | teste de vazamento medido de dentro |

Detalhes do modelo de rede, das regras de firewall e do teste de vazamento:
[`docs/remote-terminal.md`](../../docs/remote-terminal.md).
