# HTTPS

O `docker compose up -d` já sobe com HTTPS. Um proxy (Caddy, serviço `proxy`) atende:

```
https://<host>/        → front
https://<host>/api/*   → API
```

Os dois no **mesmo endereço**. Isso não é só estética: mesma origem significa **sem CORS**
no caminho e um **cookie de sessão que vale no endereço inteiro** — os dois problemas que
apareciam ao alternar entre o IP da LAN e o da VPN, cada um com sua sessão.

As portas 3000 (front) e 8000 (API) continuam publicadas em HTTP para quem prefere
acessar direto, e o mesmo build do front funciona dos dois jeitos: pelo proxy ele chama
`/api` na própria origem; pela porta 3000, chama `host:8000`.

## O que você precisa escolher

| Como você acessa | `.env` | Certificado |
|---|---|---|
| IP da LAN/VPN, `localhost`, nome de rede local | nada (padrão) | CA interna do Caddy — o navegador avisa até você confiar nela |
| Domínio público | `CADDY_SITE=app.seudominio.com` e `CADDY_TLS=seu@email` | Let's Encrypt, automático e já confiável |

Portas 80/443 ocupadas? `HTTPS_PORT=8443` e `HTTP_PORT=8080`, por exemplo.

## Confiar no certificado local (IP/LAN)

Com `CADDY_TLS=internal`, o Caddy cria uma autoridade certificadora própria. Instale a
raiz dela nas máquinas que vão usar o app e o aviso some:

```bash
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./aiworkspace-ca.crt
```

- **Windows:** duplo clique → Instalar Certificado → Máquina Local → "Autoridades de
  Certificação Raiz Confiáveis".
- **Linux:** copie para `/usr/local/share/ca-certificates/` e rode `sudo update-ca-certificates`.
- **Android/iOS:** envie o arquivo para o aparelho e instale como certificado de CA.

Sem instalar, tudo funciona igual: o navegador só mostra um aviso na primeira visita.

## Domínio público

```env
CADDY_SITE=app.seudominio.com
CADDY_TLS=seu@email.com
WEB_ORIGIN=https://app.seudominio.com
```

O DNS precisa apontar para a máquina e as portas 80 e 443 precisam estar abertas — é
assim que o Let's Encrypt valida o domínio. A renovação é automática.

## Verificação rápida

```bash
curl -k https://localhost/api/health     # {"status":"ok"}
docker compose logs proxy | tail
```

Se o `up` falhar em "port is already allocated", outra coisa na máquina está usando
80/443: troque `HTTPS_PORT`/`HTTP_PORT`.
