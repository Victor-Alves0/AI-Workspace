"""Codespace: guardas do code.exec.run (puro/hermético, sem DB nem rede).

Cobre o que Victor pediu: (1) root/serviço de sistema (sudo/apt/docker) NÃO roda no
sandbox → erro acionável apontando o mise, em vez de deixar o shell falhar; (2) o
detector de root só pega o comando na POSIÇÃO de comando, não a palavra dentro de um
argumento (senão `npm run docker:build` seria bloqueado por engano).
"""
from __future__ import annotations

import pytest

from aiworkspace.tools.sift_service import _needs_root_exec, _is_risky_exec


@pytest.mark.parametrize("cmd", [
    "sudo apt install docker",
    "apt-get install -y jq",
    "apt update && apt install curl",
    "dpkg -i pkg.deb",
    "yum install nginx",
    "dnf install git",
    "apk add bash",
    "docker build -t app .",
    "docker compose up -d",
    "docker-compose up",
    "systemctl restart nginx",
    "service postgres start",
    "cd /app && sudo make install",
    "mount /dev/sda1 /mnt",
])
def test_root_commands_are_refused(cmd):
    err = _needs_root_exec(cmd)
    assert err is not None
    assert "mise" in err  # aponta o caminho certo (rootless)


@pytest.mark.parametrize("cmd", [
    "npm run docker:build",        # 'docker' é parte do nome do script, não o comando
    "python service.py",           # 'service' é um arquivo
    "echo 'use docker in prod'",   # dentro de string
    "git commit -m 'add apt notes'",
    "mise use -g java@21 maven",
    "pytest -k auth",
    "npm install",
    "./gradlew build",
    "node server.js",
])
def test_non_root_commands_pass(cmd):
    assert _needs_root_exec(cmd) is None


def test_install_is_risky_but_not_root():
    # install de libs (rootless) NÃO é bloqueado como root, mas É "arriscado"
    # (baixa/instala) → confirma só quando o toggle global estiver ligado.
    assert _needs_root_exec("pip install requests") is None
    assert _is_risky_exec("pip install requests") is True
