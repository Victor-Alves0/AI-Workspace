"""Job Objects do Windows: limites e "mata a árvore inteira" para processos filhos.

No Linux, o isolamento dos processos que o app sobe (comandos do Codespace, código
do modelo, navegador headless) vem de rlimits + grupo de processos. O Windows não tem
nada disso; o equivalente é o Job Object:

  - KILL_ON_JOB_CLOSE: fechado o job (ou morto o nosso processo), o Windows encerra
    TODOS os processos dele — inclusive netos que o filho criou. Resolve o caso do
    `msedge.exe`, que repassa a execução a outro processo e sai: um `kill` no PID que
    subimos não alcançava o navegador de verdade, que ficava órfão.
  - limites de memória (por processo) e de CPU (tempo total do job).

O processo nasce SUSPENSO, entra no job e só então é retomado: assim nem o primeiro
filho que ele criar escapa (sem isso, havia corrida entre criar e atribuir).

Só ctypes, sem dependência nova. Fora do Windows, `JobObject` não existe e quem chama
usa o caminho POSIX.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"

# flags de criação para o subprocess.Popen: nasce suspenso (retomado em `adopt`)
CREATE_SUSPENDED = 0x00000004

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    _JobObjectBasicLimitInformation = 2
    _JobObjectExtendedLimitInformation = 9
    _LIMIT_PROCESS_TIME = 0x00000002
    _LIMIT_JOB_TIME = 0x00000004
    _LIMIT_PROCESS_MEMORY = 0x00000100
    _LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype = ctypes.c_long
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

    class JobObject:
        """Um job com KILL_ON_JOB_CLOSE e limites opcionais.

        `cpu_seconds`: teto de CPU de CADA processo do job (0 = sem teto).
        `memory_mb`:   teto de memória comprometida de CADA processo (0 = sem teto).
        """

        def __init__(self, *, cpu_seconds: int = 0, memory_mb: int = 0) -> None:
            h = _k32.CreateJobObjectW(None, None)
            if not h:
                raise ctypes.WinError(ctypes.get_last_error())
            self._h = h
            info = _EXTENDED_LIMIT()
            flags = _LIMIT_KILL_ON_JOB_CLOSE | _LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            if cpu_seconds > 0:
                flags |= _LIMIT_PROCESS_TIME
                info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_seconds) * 10_000_000
            if memory_mb > 0:
                flags |= _LIMIT_PROCESS_MEMORY
                info.ProcessMemoryLimit = int(memory_mb) * 1024 * 1024
            info.BasicLimitInformation.LimitFlags = flags
            if not _k32.SetInformationJobObject(h, _JobObjectExtendedLimitInformation,
                                                ctypes.byref(info), ctypes.sizeof(info)):
                err = ctypes.get_last_error()
                _k32.CloseHandle(h)
                raise ctypes.WinError(err)

        def adopt(self, proc) -> None:
            """Põe no job um Popen criado com CREATE_SUSPENDED e o retoma."""
            handle = wintypes.HANDLE(int(proc._handle))
            if not _k32.AssignProcessToJobObject(self._h, handle):
                err = ctypes.get_last_error()
                proc.kill()
                raise ctypes.WinError(err)
            if _ntdll.NtResumeProcess(handle) < 0:
                proc.kill()
                raise OSError("não foi possível retomar o processo suspenso")

        def terminate(self, code: int = 1) -> None:
            """Encerra todos os processos do job agora."""
            if self._h:
                _k32.TerminateJobObject(self._h, code)

        def close(self) -> None:
            """Fecha o job — pelo KILL_ON_JOB_CLOSE, o que ainda roda nele morre."""
            if self._h:
                _k32.CloseHandle(self._h)
                self._h = None

        def __del__(self) -> None:  # rede de segurança: nunca deixa a árvore órfã
            try:
                self.close()
            except Exception:  # noqa: BLE001
                pass
