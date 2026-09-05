# Remote Terminal

Um terminal nas **suas** máquinas — uma VPS, o servidor de casa, uma workstation — para a
IA administrar, instalar, depurar e ler logs onde as coisas realmente rodam. Um agente é
instalado na máquina; o AI Workspace fala com ele por uma API HTTPS autenticada por token.

Isso é diferente da ferramenta **Executar no Projeto** (`code.exec.run`): aquela roda num
sandbox do servidor, sem privilégio e descartável. Aqui é a máquina de verdade, com a rede
dela, os dados dela e o software dela instalado.

## Por que um agente e não SSH

SSH dá um shell interativo completo e uma credencial que costuma valer para tudo. O agente
dá:

- **superfície pequena** — `exec`, `jobs` e `egress`, nada mais;
- **token por-máquina, revogável** sem mexer em `authorized_keys`;
- **privilégio rebaixado por padrão** — os comandos rodam como um usuário dedicado, não como
  quem instalou;
- e a parte que o SSH não tem: **controle de saída de rede com killswitch** aplicado
  localmente, do lado onde os comandos de fato saem.

## As duas camadas de rede

Elas são independentes, protegem coisas diferentes, e **cada uma tem seu killswitch**.

### 1. Do AI Workspace até a máquina

"Sempre falar com X usando Y." O servidor pode discar através de um proxy SOCKS5/HTTP (um
Tor, um `ssh -D`, um Shadowsocks), de modo que a máquina e o caminho até ela não vejam o IP
do servidor. O nome do host é resolvido **pelo proxy**, não localmente — resolver aqui
vazaria o alvo para o resolvedor do servidor mesmo com o tráfego tunelado.

Ligue **Exigir o proxy** para tornar isso uma garantia: sem proxy utilizável, a conexão
simplesmente não acontece. **Nunca há tentativa direta** — é justamente ela que exporia o
IP, e uma tentativa "só dessa vez" não teria como ser percebida.

Configuração: Configurações → Integrações → **Remote Terminal** → a máquina → *Como o AI
Workspace fala com esta máquina*.

### 2. Da máquina para fora (a saída dos comandos)

Vale para tudo que os comandos fizerem: um `curl`, um `git clone`, um `apt`, um scanner.
Três modos:

| Modo | O que faz | Vaza? |
|------|-----------|-------|
| **Sem restrição** | nada é aplicado | a máquina sai como sempre |
| **Só variáveis** | injeta `http_proxy`/`https_proxy`/`all_proxy` (como `socks5h://`, DNS no proxy) | **sim**, se um programa ignorar as variáveis ou resolver DNS por conta própria |
| **Selada + killswitch** | as variáveis **mais** uma regra de firewall por-uid que descarta toda saída fora do proxy | não: o que escapar do env morre no firewall |

No modo selado, se o proxy cair o agente **recusa executar** em vez de deixar o comando sair
pela rota normal. A recusa volta com o motivo, e a IA é instruída a repassá-lo em vez de
tentar de novo às cegas.

Com **DNS pelo proxy** ligado (padrão), as portas 53/udp e 53/tcp ficam fechadas para o
usuário dos comandos: nenhuma consulta de nome sai da máquina.

## Como as regras funcionam

Os comandos rodam como um usuário dedicado (`aiw-remote`), e o firewall filtra **exatamente
aquele uid**:

```
table inet aiw_egress {
  chain output {
    type filter hook output priority 0; policy accept;
    meta skuid != <uid> accept        # o resto da máquina não sente a regra
    oifname "lo" accept
    ip daddr <ip do proxy> tcp dport <porta> accept
    reject                            # nada mais sai
  }
}
```

Em kernels sem `nft`, o mesmo é montado numa cadeia `AIW_EGRESS` do `iptables` com
`-m owner --uid-owner`.

Duas consequências que valem entender antes de instalar:

- **O modo selado exige o usuário dedicado.** Filtrar o uid 0 derrubaria a rede da máquina
  inteira (seus serviços, seu SSH), então o agente recusa e explica em vez de fazer isso.
- **`sudo` anula o killswitch** nesse modo: `sudo` vira uid 0 e escapa da regra que casa
  pelo uid. Por isso `--grant-sudo` e `--force-egress` são incompatíveis no instalador — uma
  proteção que não protege é pior que nenhuma.

## Instalação

Na sua máquina:

```bash
scp -r apps/remote-agent root@SEU_IP:/tmp/
ssh root@SEU_IP
cd /tmp/remote-agent

# terminal simples
sudo ./install.sh --san SEU_IP

# terminal com a saída selada por um proxy local (Tor, túnel, Shadowsocks)
sudo ./install.sh --san SEU_IP --proxy socks5h://127.0.0.1:9050 --force-egress
```

O instalador cria o usuário dedicado, gera token e certificado TLS, sobe o serviço systemd
e imprime o que você cola no app: **endereço, token e certificado**.

Depois, no AI Workspace: Configurações → Integrações → **Remote Terminal** → *Adicionar*.
Use **Testar** para conferir a conexão e **Testar vazamento** para conferir a saída.

Para remover: `sudo ./install.sh --uninstall` (tira o serviço **e as regras de firewall** —
esquecê-las deixaria o usuário dedicado sem rede sem ninguém lembrar por quê).

## O teste de vazamento

Roda **de dentro** da máquina, como o usuário dos comandos, pelo mesmo caminho de execução —
medir pelo agente (que é root e não está sujeito às regras) não provaria nada. Ele reporta:

- o IP que o destino vê **através do proxy**;
- se uma conexão **direta** consegue sair — o resultado desejado é **falhar**;
- se o **DNS local** responde — no modo selado com DNS pelo proxy, deve falhar.

Um "passou ✗" na conexão direta significa que as regras não estão cobrindo o usuário dos
comandos: a política está prometendo mais do que entrega.

## TLS

O certificado gerado pelo instalador é autoassinado — é a sua máquina, não um site. O modo
padrão **`pinned`** valida contra aquele certificado específico: verificação real de chave,
sem depender de CA pública e sem exigir que o SAN case com o endereço (a máquina é alcançada
ora pelo IP, ora por um túnel, ora por `127.0.0.1`). Use `system` se o agente estiver atrás
de um proxy reverso com certificado público, e `off` apenas dentro de um túnel confiável.

## A ferramenta, do ponto de vista do modelo

`remote.terminal.run`, com `action`:

| Ação | Para quê |
|------|----------|
| `hosts` | lista as máquinas com status e política de saída — o primeiro passo quando há dúvida |
| `run` | roda um comando e devolve saída + `exit_code` |
| `start` | dispara um comando longo em segundo plano (devolve `job_id`) |
| `job` | status/saída de um job; `wait=<s>` bloqueia até terminar |
| `kill` | mata um job |
| `egress` | reporta a política; `test=true` roda o teste de vazamento |

Cada máquina tem um **slug** (`vps-oracle`) — é assim que o modelo a escolhe. Com mais de
uma máquina e sem `host`, a ferramenta **pergunta** em vez de chutar: rodar o comando certo
na máquina errada não é um erro recuperável.

## Confirmação e escopo

- **Por máquina:** *Pedir confirmação a cada comando* vem **ligada**. Diferente do sandbox
  do Codespace, o padrão aqui é perguntar.
- **Por modelo:** na engrenagem da ferramenta, escolha quais máquinas aquele modelo alcança
  e quais ações pode usar (`run`, `start`, consultar jobs/rede). Um modelo pode exigir
  confirmação mesmo numa máquina marcada como "não perguntar" — o piso soma, não substitui.

## Referências

- Agente e instalador: [`apps/remote-agent/`](../apps/remote-agent/)
- Cliente e política do lado do servidor: `apps/server/aiworkspace/remote/`
- Modelo de dados: `apps/server/aiworkspace/models/remote_host.py`
