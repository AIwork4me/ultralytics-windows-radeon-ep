"""Windows Job Object process-tree lifecycle (mission R7-B).

Every EP attempt runs inside its own Job Object so that a wedged worker and
*everything it spawned* can be reclaimed deterministically. The previous
implementation used subprocess.run(..., timeout=...) which kills only the
direct child: a worker that starts a PyTorch oracle (or any grandchild) leaves
orphans behind and the parent has no handle with which to reap them.

Design rules (from the mission and AGENTS.md rule 5):

  * one job per attempt, named with the attempt id, never shared;
  * JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE so closing our handle terminates the
    whole tree even on an abnormal parent exit;
  * the child is created CREATE_SUSPENDED, assigned to the job, and only then
    resumed, closing the classic assign-after-exec race;
  * cleanup is bounded: TerminateJobObject -> wait -> TerminateProcess on the
    direct child only if the job path failed;
  * we never enumerate and kill unrelated python.exe processes.

Everything here is optional-by-failure: if the job cannot be created or the
assignment is refused (for example a locked-down CI agent), the spawn still
happens and the attempt is marked job_assigned=False with the concrete Win32
error. A degraded container is recorded, never silently upgraded to a clean one.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

# --- Win32 constants --------------------------------------------------------------
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SUSPEND_RESUME = 0x0800
THREAD_SUSPEND_RESUME = 0x0002

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPTHREAD = 0x00000004

JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800

JobObjectBasicAccountingInformation = 1
JobObjectBasicProcessIdList = 3
JobObjectExtendedLimitInformation = 9

STILL_ACTIVE = 259

NUL = bytes(1)  # b"\x00", written without an escape so the source stays literal


class _LARGE_INTEGER(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", _LARGE_INTEGER),
        ("PerJobUserTimeLimit", _LARGE_INTEGER),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", _LARGE_INTEGER),
        ("TotalKernelTime", _LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", _LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", _LARGE_INTEGER),
        ("TotalPageFaultCount", wt.DWORD),
        ("TotalProcesses", wt.DWORD),
        ("ActiveProcesses", wt.DWORD),
        ("TotalTerminatedProcesses", wt.DWORD),
    ]


class _JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
    _fields_ = [
        ("NumberOfAssignedProcesses", wt.DWORD),
        ("NumberOfProcessIdsInList", wt.DWORD),
        ("ProcessIdList", ctypes.c_size_t * 1),
    ]


class _THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ThreadID", wt.DWORD),
        ("th32OwnerProcessID", wt.DWORD), ("tpBasePri", wt.LONG),
        ("tpDeltaPri", wt.LONG), ("dwFlags", wt.DWORD),
    ]


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
_k32.CreateJobObjectW.restype = wt.HANDLE
_k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
_k32.SetInformationJobObject.restype = wt.BOOL
_k32.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                          wt.DWORD, ctypes.POINTER(wt.DWORD)]
_k32.QueryInformationJobObject.restype = wt.BOOL
_k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
_k32.AssignProcessToJobObject.restype = wt.BOOL
_k32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
_k32.TerminateJobObject.restype = wt.BOOL
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.CloseHandle.restype = wt.BOOL
_k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.OpenProcess.restype = wt.HANDLE
_k32.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.OpenThread.restype = wt.HANDLE
_k32.ResumeThread.argtypes = [wt.HANDLE]
_k32.ResumeThread.restype = wt.DWORD
_k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
_k32.TerminateProcess.restype = wt.BOOL
_k32.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
_k32.IsProcessInJob.restype = wt.BOOL
_k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
_k32.GetExitCodeProcess.restype = wt.BOOL
_k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_k32.Thread32First.argtypes = [wt.HANDLE, ctypes.POINTER(_THREADENTRY32)]
_k32.Thread32First.restype = wt.BOOL
_k32.Thread32Next.argtypes = [wt.HANDLE, ctypes.POINTER(_THREADENTRY32)]
_k32.Thread32Next.restype = wt.BOOL
_k32.Process32First.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32)]
_k32.Process32First.restype = wt.BOOL
_k32.Process32Next.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32)]
_k32.Process32Next.restype = wt.BOOL


def _err() -> str:
    return "win32_error=" + str(ctypes.get_last_error())


# --- process tree enumeration -----------------------------------------------------
@dataclass
class ProcRow:
    pid: int
    ppid: int
    name: str

    def as_dict(self) -> dict:
        return {"pid": self.pid, "ppid": self.ppid, "name": self.name}


def _invalid_handle(h) -> bool:
    return (not h) or h == wt.HANDLE(-1).value


def snapshot_processes() -> list[ProcRow]:
    """Toolhelp process snapshot; never raises (an empty list is honest)."""
    rows: list[ProcRow] = []
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if _invalid_handle(snap):
        return rows
    try:
        pe = _PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        if not _k32.Process32First(snap, ctypes.byref(pe)):
            return rows
        while True:
            name = bytes(pe.szExeFile).split(NUL, 1)[0].decode("mbcs", "replace")
            rows.append(ProcRow(pid=int(pe.th32ProcessID),
                                ppid=int(pe.th32ParentProcessID), name=name))
            if not _k32.Process32Next(snap, ctypes.byref(pe)):
                break
    finally:
        _k32.CloseHandle(snap)
    return rows


def descendants_of(root_pid: int, rows: list[ProcRow] | None = None) -> list[ProcRow]:
    """All transitive descendants of root_pid from one snapshot."""
    rows = snapshot_processes() if rows is None else rows
    children: dict[int, list[ProcRow]] = {}
    for r in rows:
        children.setdefault(r.ppid, []).append(r)
    out: list[ProcRow] = []
    stack = [root_pid]
    seen = {root_pid}
    while stack:
        cur = stack.pop()
        for c in children.get(cur, []):
            if c.pid in seen:
                continue
            seen.add(c.pid)
            out.append(c)
            stack.append(c.pid)
    return out


_ntdll = ctypes.WinDLL("ntdll")
_ntdll.NtResumeProcess.argtypes = [wt.HANDLE]
_ntdll.NtResumeProcess.restype = ctypes.c_long


def _resume_process_threads(pid: int) -> dict:
    """Resume a CREATE_SUSPENDED child, tolerating the start-up race.

    A Toolhelp snapshot taken immediately after CreateProcess can still miss the
    brand-new thread, which would leave the child suspended until its wall-clock
    timeout - a real wedge observed during R7 evidence generation. So: retry the
    snapshot for a bounded window, and if the thread is still not visible fall
    back to NtResumeProcess on the process handle.
    """
    for attempt in range(40):
        out = _resume_once(pid)
        if out["resumed_threads"]:
            out["attempts"] = attempt + 1
            return out
        time.sleep(0.01)
    out = _resume_once(pid)
    out["attempts"] = 40
    if not out["resumed_threads"]:
        h = _k32.OpenProcess(PROCESS_SUSPEND_RESUME | PROCESS_QUERY_LIMITED_INFORMATION,
                             False, int(pid))
        if h:
            try:
                status = _ntdll.NtResumeProcess(h)
                out["nt_resume_process"] = "ok" if status == 0 else ("status=0x%08X"
                                                                   % (status & 0xFFFFFFFF))
            finally:
                _k32.CloseHandle(h)
        else:
            out["nt_resume_process"] = "open_process_failed"
    return out


def _resume_once(pid: int) -> dict:
    """One Toolhelp pass: resume every suspended thread of pid."""
    resumed, failed = 0, []
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if _invalid_handle(snap):
        return {"resumed_threads": 0, "resume_errors": ["snapshot_failed"], "pid": pid}
    try:
        te = _THREADENTRY32()
        te.dwSize = ctypes.sizeof(_THREADENTRY32)
        ok = _k32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if int(te.th32OwnerProcessID) == int(pid):
                h = _k32.OpenThread(THREAD_SUSPEND_RESUME, False, int(te.th32ThreadID))
                if not h:
                    failed.append(_err())
                else:
                    try:
                        if _k32.ResumeThread(h) == 0xFFFFFFFF:
                            failed.append(_err())
                        else:
                            resumed += 1
                    finally:
                        _k32.CloseHandle(h)
            ok = _k32.Thread32Next(snap, ctypes.byref(te))
    finally:
        _k32.CloseHandle(snap)
    return {"resumed_threads": resumed, "resume_errors": failed, "pid": pid}


def process_alive(pid: int) -> bool:
    """Read-only liveness probe (tests assert no survivors with this)."""
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = wt.DWORD(0)
        if not _k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return int(code.value) == STILL_ACTIVE
    finally:
        _k32.CloseHandle(h)


# --- the job ----------------------------------------------------------------------
class JobObject:
    """A named Windows Job Object holding exactly one attempt's process tree."""

    def __init__(self, *, label: str = "attempt", kill_on_close: bool = True,
                 active_process_limit: int | None = None,
                 allow_breakaway: bool = False):
        self.label = label
        self.name = "radeon-ort-" + label + "-" + uuid.uuid4().hex[:12]
        self.handle = None
        self.error: str | None = None
        self.limits = {
            "kill_on_close": bool(kill_on_close),
            "active_process_limit": active_process_limit,
            "allow_breakaway": bool(allow_breakaway),
        }
        self._closed = False
        self.handle = _k32.CreateJobObjectW(None, self.name)
        if not self.handle:
            self.error = "CreateJobObjectW failed (" + _err() + ")"
            return
        flags = JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        if kill_on_close:
            flags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if allow_breakaway:
            flags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK
        if active_process_limit:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = flags
        if active_process_limit:
            info.BasicLimitInformation.ActiveProcessLimit = int(active_process_limit)
        ok = _k32.SetInformationJobObject(
            self.handle, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            self.error = "SetInformationJobObject failed (" + _err() + ")"
        self.limits["limit_flags"] = f"0x{info.BasicLimitInformation.LimitFlags:08X}"

    # -- lifecycle -----------------------------------------------------------
    def assign(self, pid: int) -> dict:
        """Assign an existing process to the job (after a suspended spawn)."""
        if not self.handle:
            return {"ok": False, "error": self.error or "no job handle"}
        h = _k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE |
                             PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return {"ok": False, "error": "OpenProcess(" + str(pid) + ") failed (" + _err() + ")"}
        try:
            ok = bool(_k32.AssignProcessToJobObject(self.handle, h))
            if not ok:
                return {"ok": False,
                        "error": "AssignProcessToJobObject failed (" + _err() + ")"}
            in_job = wt.BOOL(False)
            _k32.IsProcessInJob(h, self.handle, ctypes.byref(in_job))
            return {"ok": True, "in_job": bool(in_job.value), "pid": int(pid)}
        finally:
            _k32.CloseHandle(h)

    def terminate(self, exit_code: int = 1) -> dict:
        if not self.handle:
            return {"ok": False, "error": self.error or "no job handle"}
        ok = bool(_k32.TerminateJobObject(self.handle, int(exit_code)))
        return {"ok": ok, "error": None if ok else _err(), "exit_code": int(exit_code)}

    def stats(self) -> dict:
        if not self.handle:
            return {"available": False, "error": self.error}
        acct = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        ok = _k32.QueryInformationJobObject(
            self.handle, JobObjectBasicAccountingInformation,
            ctypes.byref(acct), ctypes.sizeof(acct), None)
        if not ok:
            return {"available": False, "error": _err()}
        return {
            "available": True,
            "total_processes": int(acct.TotalProcesses),
            "active_processes": int(acct.ActiveProcesses),
            "total_terminated": int(acct.TotalTerminatedProcesses),
            "total_page_faults": int(acct.TotalPageFaultCount),
        }

    def pids(self) -> list[int]:
        """PIDs currently assigned to the job (an audit list, never a kill list)."""
        if not self.handle:
            return []
        cap = 512
        size = (ctypes.sizeof(_JOBOBJECT_BASIC_PROCESS_ID_LIST)
                + cap * ctypes.sizeof(ctypes.c_size_t))
        buf = ctypes.create_string_buffer(size)
        ok = _k32.QueryInformationJobObject(
            self.handle, JobObjectBasicProcessIdList, buf, size, None)
        if not ok:
            return []
        lst = ctypes.cast(buf, ctypes.POINTER(_JOBOBJECT_BASIC_PROCESS_ID_LIST)).contents
        n = min(int(lst.NumberOfProcessIdsInList), cap)
        base_off = _JOBOBJECT_BASIC_PROCESS_ID_LIST.ProcessIdList.offset
        arr = (ctypes.c_size_t * n).from_buffer(buf, base_off)
        return [int(arr[i]) for i in range(n)]

    def close(self) -> None:
        if self.handle and not self._closed:
            _k32.CloseHandle(self.handle)
            self._closed = True
        self.handle = None

    # -- context manager -----------------------------------------------------
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):  # best-effort; explicit close is always preferred
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


# --- spawn helper ------------------------------------------------------------------
@dataclass
class SpawnResult:
    pid: int | None = None
    returncode: int | None = None
    timed_out: bool = False
    duration_s: float = 0.0
    job_assigned: bool = False
    job_error: str | None = None
    suspended_start: bool = False
    resume: dict = field(default_factory=dict)
    job_stats_before_cleanup: dict = field(default_factory=dict)
    job_stats_after_cleanup: dict = field(default_factory=dict)
    survivors: list[int] = field(default_factory=list)
    descendants_at_spawn: list[dict] = field(default_factory=list)
    terminated_by_job: bool = False
    forced_kill: bool = False
    cancelled: bool = False
    container: str = "job_object"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "pid": self.pid, "returncode": self.returncode,
            "timed_out": self.timed_out, "duration_s": round(self.duration_s, 4),
            "job_assigned": self.job_assigned, "job_error": self.job_error,
            "suspended_start": self.suspended_start, "container": self.container,
            "terminated_by_job": self.terminated_by_job,
            "forced_kill": self.forced_kill, "cancelled": self.cancelled,
            "survivors": self.survivors,
            "descendants_at_spawn": self.descendants_at_spawn,
            "resume": self.resume, "notes": self.notes,
            "job_stats_before_cleanup": self.job_stats_before_cleanup,
            "job_stats_after_cleanup": self.job_stats_after_cleanup,
        }


def _survivors(job: JobObject, root_pid: int | None) -> list[int]:
    """Live processes still attributable to this attempt after cleanup.

    The job's PID list can briefly retain an already-exited pid, so every entry
    is re-checked with a read-only liveness probe. With no job handle we fall
    back to a Toolhelp descendant scan of the direct child.
    """
    self_pid = os.getpid()
    if job.handle:
        cands = [p for p in job.pids() if p != self_pid]
    else:
        cands = [r.pid for r in descendants_of(root_pid)] if root_pid else []
    return [p for p in cands if process_alive(p)]


class _Cancelled(Exception):
    """Internal: the caller asked us to stop; nothing to propagate."""


_CANCELLED = -9999


def _wait_bounded(proc, timeout_s: float, cancel_event, poll_s: float) -> int:
    """Wait up to timeout_s while honouring an optional cancel event."""
    deadline = time.perf_counter() + float(timeout_s)
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return _CANCELLED
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout_s)
        slice_s = min(poll_s, remaining)
        try:
            return int(proc.wait(timeout=slice_s))
        except subprocess.TimeoutExpired:
            continue


def spawn_in_job(argv: list[str], *, cwd: str | Path | None = None,
                 log_path: str | Path | None = None, timeout_s: float = 900,
                 env: dict | None = None, label: str = "attempt",
                 active_process_limit: int | None = None,
                 kill_grace_s: float = 5.0,
                 close_job: bool = True,
                 cancel_event=None,
                 poll_s: float = 0.05) -> SpawnResult:
    """Run argv inside a fresh Job Object with a hard wall-clock timeout.

    The child is started suspended, assigned to the job, then resumed. On
    timeout the whole job is terminated; the direct child is only force-killed
    if job termination could not be performed. The returned evidence records
    the job's own accounting so a caller can prove no descendant survived.
    """
    res = SpawnResult()
    job = JobObject(label=label, kill_on_close=True,
                    active_process_limit=active_process_limit)
    if job.error:
        res.container = "plain_subprocess"
        res.job_error = job.error
        res.notes.append("job container unavailable; fell back to plain subprocess")
    log_fh = open(log_path, "ab") if log_path else subprocess.DEVNULL  # noqa: SIM115
    t0 = time.perf_counter()
    proc = None
    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd) if cwd else None,
            stdout=log_fh, stderr=subprocess.STDOUT,
            creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW,
            env=env, close_fds=True)
        res.pid = int(proc.pid)
        res.suspended_start = True
        if not job.error:
            a = job.assign(res.pid)
            res.job_assigned = bool(a.get("ok"))
            if not a.get("ok"):
                res.job_error = str(a.get("error"))
                res.notes.append("job assignment failed; cleanup falls back to the "
                                 "direct child only")
        res.resume = _resume_process_threads(res.pid)
        if not res.resume.get("resumed_threads"):
            res.notes.append("RESUME_FAILED: no suspended thread resumed and "
                             "NtResumeProcess did not report success")
        try:
            res.returncode = _wait_bounded(proc, timeout_s, cancel_event, poll_s)
            if res.returncode == _CANCELLED:
                res.cancelled = True
                res.returncode = 130
                res.job_stats_before_cleanup = job.stats() if job.handle else {}
                if job.handle:
                    res.terminated_by_job = bool(job.terminate(130).get("ok"))
                try:
                    proc.wait(timeout=kill_grace_s)
                except subprocess.TimeoutExpired:
                    res.forced_kill = True
                    proc.kill()
                res.notes.append("cancelled by caller; job terminated")
                raise _Cancelled()
        except subprocess.TimeoutExpired:
            res.timed_out = True
            res.job_stats_before_cleanup = job.stats() if job.handle else {}
            if job.handle:
                term = job.terminate(1)
                res.terminated_by_job = bool(term.get("ok"))
            try:
                proc.wait(timeout=kill_grace_s)
            except subprocess.TimeoutExpired:
                res.forced_kill = True
                proc.kill()
                try:
                    proc.wait(timeout=kill_grace_s)
                except subprocess.TimeoutExpired:
                    res.notes.append("direct child survived the TerminateProcess window")
            res.returncode = 124
        res.job_stats_after_cleanup = job.stats() if job.handle else {}
        res.survivors = _survivors(job, res.pid)
        res.duration_s = time.perf_counter() - t0
        return res
    except _Cancelled:
        res.job_stats_after_cleanup = job.stats() if job.handle else {}
        res.survivors = _survivors(job, res.pid)
        res.duration_s = time.perf_counter() - t0
        return res
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()  # defensive: never leave a child of ours running
            except OSError:
                pass
        if close_job:
            job.close()
        if log_path:
            try:
                log_fh.close()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass