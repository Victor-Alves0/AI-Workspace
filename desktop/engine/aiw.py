"""Bootstrap do CLI `aiw` (roda com o Python do motor).

O backend que roda pode ser o da atualização leve (<dados>/appcode), não o embarcado:
o CLI usa a MESMA regra do launcher para escolher, senão ficaria uma versão atrás.
Por isso nada de `aiworkspace` é importado antes de o sys.path estar montado — o
Python guardaria o pacote embarcado em cache e o overlay seria ignorado.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["AIW_ENGINE_ROOT"] = str(ROOT)


def _data_dir() -> Path:
    # mesma regra de aiworkspace.cli.client.data_dir (não dá para importá-la daqui)
    if v := os.environ.get("AIW_DATA_DIR"):
        return Path(v)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "com.aiworkspace.app" / "data"


if (ROOT / "launcher.py").exists():  # Linux; no Windows a atualização é aplicada no lugar
    import launcher  # noqa: E402 - a pasta do script já está no sys.path

    site, _web, _app = launcher.code_dirs(_data_dir())
    if site is not None:
        sys.path.insert(0, str(site))

# a pasta do motor sai do sys.path: app/, web/ etc. ali não podem sombrear módulos
sys.path[:] = [p for p in sys.path if not p or Path(p).resolve() != ROOT]

from aiworkspace.cli.main import main  # noqa: E402

main()
