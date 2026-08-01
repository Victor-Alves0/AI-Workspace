"""Auto-background de comandos longos e o teto de CPU.

Gap #2 da analise do Metabase: a suite de testes/build morria com exit 152 (SIGXCPU/
RLIMIT_CPU) por rodar SINCRONA (nao detectada como longa) e sob o teto apertado. Estes
testes fecham a deteccao: suites de teste/build tem que ir pro background (onde o CPU e
generoso), e comandos triviais NAO.
"""
from __future__ import annotations

from aiworkspace.tools.sift_service import _is_long_runner


def test_test_suites_and_builds_are_long_runners():
    for cmd in [
        "./bin/test-agent --oss ':only [metabase.actions-rest.api-test]'",
        "bun run build", "npm run build", "npm test", "pnpm run test", "yarn build",
        "./node_modules/.bin/jest --config ./jest.config.js", "pytest -k auth",
        "venv/bin/pytest", "go test ./...", "mvn test", "clojure -M:run", "lein run",
        "mise install", "git clone https://x", "gradle build",
    ]:
        assert _is_long_runner(cmd), f"deveria ser longo: {cmd}"


def test_trivial_commands_are_not_long_runners():
    for cmd in [
        "ls -la", "cat file.txt", "echo hi", "grep foo bar", "git status",
        "python manage.py shell", "curl -i http://127.0.0.1:4001/api/health",
        "cabin/testcase run", "cat robin/build.txt",
    ]:
        assert not _is_long_runner(cmd), f"NAO deveria ser longo: {cmd}"
