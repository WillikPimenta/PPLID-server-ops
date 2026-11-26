"""Own only a Windows build tree; never use this for business workers.

The child starts suspended, enters a non-breakaway Job Object, then resumes.
Closing the sole non-inherited job handle kills that build tree if its owner
crashes. Normal return requires zero active processes and a durable receipt.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

import psutil

from . import processes
from .store import atomic_json, read_json, utc


class _BuildJob:
    def __init__(self):
        self.name = 'Local\\PPLID-Build-' + uuid.uuid4().hex
        self.kernel = self._kernel()
        self.handle = self.kernel.CreateJobObjectW(None, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        class BasicLimits(ctypes.Structure):
            _fields_ = [('processTime', ctypes.c_int64), ('jobTime', ctypes.c_int64),
                        ('flags', ctypes.c_uint32), ('minimumWorkingSet', ctypes.c_size_t),
                        ('maximumWorkingSet', ctypes.c_size_t), ('activeProcessLimit', ctypes.c_uint32),
                        ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32),
                        ('scheduling', ctypes.c_uint32)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('readOps', 'writeOps', 'otherOps', 'readBytes', 'writeBytes', 'otherBytes')]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', Counters), ('processMemory', ctypes.c_size_t),
                        ('jobMemory', ctypes.c_size_t), ('peakProcessMemory', ctypes.c_size_t),
                        ('peakJobMemory', ctypes.c_size_t)]
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway flags.
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    @staticmethod
    def _kernel():
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        declarations = {
            'CreateJobObjectW': ([ctypes.c_void_p, ctypes.c_wchar_p], ctypes.c_void_p),
            'OpenJobObjectW': ([ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p], ctypes.c_void_p),
            'SetInformationJobObject': ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
            'QueryInformationJobObject': ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p], ctypes.c_int),
            'TerminateJobObject': ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
            'ResumeThread': ([ctypes.c_void_p], ctypes.c_uint32),
            'CloseHandle': ([ctypes.c_void_p], ctypes.c_int),
            'InitializeProcThreadAttributeList': ([ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_size_t)], ctypes.c_int),
            'UpdateProcThreadAttribute': ([ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            'DeleteProcThreadAttributeList': ([ctypes.c_void_p], None),
            'DuplicateHandle': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_int),
            'CreateProcessW': ([ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_uint32, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            'WaitForSingleObject': ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint32),
            'GetExitCodeProcess': ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int),
            'TerminateProcess': ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
        }
        for name, (arguments, result) in declarations.items():
            method = getattr(kernel, name)
            method.argtypes, method.restype = arguments, result
        return kernel

    @staticmethod
    def _active(kernel, handle):
        class Accounting(ctypes.Structure):
            _fields_ = [('user', ctypes.c_int64), ('kernel', ctypes.c_int64),
                        ('periodUser', ctypes.c_int64), ('periodKernel', ctypes.c_int64),
                        ('faults', ctypes.c_uint32), ('total', ctypes.c_uint32),
                        ('active', ctypes.c_uint32), ('terminated', ctypes.c_uint32)]
        info = Accounting()
        if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.active

    def active(self):
        return self._active(self.kernel, self.handle)

    @classmethod
    def existing_active(cls, name):
        if not re.fullmatch(r'Local\\PPLID-Build-[0-9a-f]{32}', name):
            raise RuntimeError('Invalid durable build Job Object identity')
        kernel = cls._kernel()
        handle = kernel.OpenJobObjectW(0x0004, False, name)  # query only; never adopt/terminate another tree.
        if not handle:
            if ctypes.get_last_error() == 2:
                # Job objects are deleted only after handles and associated
                # processes are gone; absence confirms this generation ended.
                return 0
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return cls._active(kernel, handle)
        finally:
            kernel.CloseHandle(handle)

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class _BuildProcess:
    """Native CreateProcess with atomic JOB_LIST assignment (Windows 10+).

    SDK constants/structures: Microsoft win32metadata WinBase.h and
    processthreadsapi.h. HANDLE_LIST includes only duplicate log/NUL handles;
    neither the Job Object nor the deployment mutex can enter the child.
    https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
    """
    def __init__(self, job, command, stdout, environment, cwd):
        import msvcrt
        from ctypes import wintypes
        self.kernel = kernel = job.kernel
        self.command = command
        self._handle = self._thread = None
        class Startup(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('reserved', wintypes.LPWSTR), ('desktop', wintypes.LPWSTR),
                        ('title', wintypes.LPWSTR)] + [(name, wintypes.DWORD) for name in
                            ('x', 'y', 'width', 'height', 'xChars', 'yChars', 'fill', 'flags')] + [
                        ('show', wintypes.WORD), ('reservedBytes', wintypes.WORD), ('reservedPointer', ctypes.c_void_p),
                        ('stdin', wintypes.HANDLE), ('stdout', wintypes.HANDLE), ('stderr', wintypes.HANDLE)]
        class StartupExtended(ctypes.Structure):
            _fields_ = [('startup', Startup), ('attributes', ctypes.c_void_p)]
        class Information(ctypes.Structure):
            _fields_ = [('process', wintypes.HANDLE), ('thread', wintypes.HANDLE),
                        ('pid', wintypes.DWORD), ('tid', wintypes.DWORD)]
        executable = shutil.which(str(command[0]), path=environment.get('PATH'))
        if not executable:
            raise FileNotFoundError(str(command[0]))
        duplicates = []
        initialized = False
        attribute_size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(attribute_size))
        if not attribute_size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = ctypes.create_string_buffer(attribute_size.value)
        try:
            if not kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(attribute_size)):
                raise ctypes.WinError(ctypes.get_last_error())
            initialized = True
            with open(os.devnull, 'rb') as null_input:
                for source in (null_input, stdout):
                    duplicate = ctypes.c_void_p()
                    if not kernel.DuplicateHandle(ctypes.c_void_p(-1), msvcrt.get_osfhandle(source.fileno()),
                                                  ctypes.c_void_p(-1), ctypes.byref(duplicate), 0, True, 2):
                        raise ctypes.WinError(ctypes.get_last_error())
                    duplicates.append(duplicate.value)
                inherited = (ctypes.c_void_p * 2)(*duplicates)
                jobs = (ctypes.c_void_p * 1)(job.handle)
                # ProcThreadAttributeValue(number,FALSE,TRUE,FALSE), SDK
                # numbers HandleList=2 / JobList=13; INPUT flag=0x00020000.
                for key, value in ((0x00020002, inherited), (0x0002000D, jobs)):
                    if not kernel.UpdateProcThreadAttribute(attributes, 0, key, value, ctypes.sizeof(value), None, None):
                        raise ctypes.WinError(ctypes.get_last_error())
                startup = StartupExtended()
                startup.startup.cb = ctypes.sizeof(startup)
                startup.startup.flags = 0x00000100  # STARTF_USESTDHANDLES
                startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = duplicates[0], duplicates[1], duplicates[1]
                startup.attributes = ctypes.cast(attributes, ctypes.c_void_p)
                information = Information()
                environment_block = ctypes.create_unicode_buffer('\0'.join(
                    f'{key}={value}' for key, value in sorted(environment.items(), key=lambda pair: pair[0].upper())) + '\0\0')
                line = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(arg) for arg in command]))
                # CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT |
                # EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW.
                flags = 0x00000004 | 0x00000400 | 0x00080000 | 0x08000000
                if not kernel.CreateProcessW(executable, line, None, None, True, flags, environment_block,
                                              str(cwd) if cwd else None, ctypes.byref(startup), ctypes.byref(information)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._handle, self._thread, self.pid = information.process, information.thread, information.pid
        finally:
            if initialized:
                kernel.DeleteProcThreadAttributeList(attributes)
            for handle in duplicates:
                kernel.CloseHandle(handle)

    def resume(self):
        previous = self.kernel.ResumeThread(self._thread)
        if previous == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        if previous != 1:
            raise RuntimeError('Unexpected suspend count on build primary thread')
        self.kernel.CloseHandle(self._thread)
        self._thread = None

    def poll(self):
        status = self.kernel.WaitForSingleObject(self._handle, 0)
        if status == 258:
            return None
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = ctypes.c_uint32()
        if not self.kernel.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def wait(self, timeout):
        result = self.kernel.WaitForSingleObject(self._handle, int(timeout * 1000))
        if result == 258:
            raise subprocess.TimeoutExpired(self.command, timeout)
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()

    def close(self):
        for name in ('_thread', '_handle'):
            handle = getattr(self, name)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)


def verify_previous(receipt):
    """Never overwrite an uncertain launch, even after its owner disappears."""
    if not receipt.exists():
        return
    previous = read_json(receipt)
    if previous.get('treeEmpty') is True and previous.get('phase') in ('complete', 'failed'):
        return
    owner = previous.get('owner') or {}
    if type(owner.get('pid')) is not int or not isinstance(owner.get('created'), (float, int)):
        raise RuntimeError('BUILD_RECOVERY_REQUIRED: previous build owner identity is incomplete')
    try:
        existing_owner = psutil.Process(owner['pid'])
        if abs(existing_owner.create_time() - owner['created']) <= .01:
            raise RuntimeError('BUILD_RECOVERY_REQUIRED: previous build owner is still alive')
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied as error:
        raise RuntimeError('BUILD_RECOVERY_REQUIRED: previous build owner liveness is unknown') from error
    if not previous.get('jobName') or _BuildJob.existing_active(previous['jobName']) != 0:
        raise RuntimeError('BUILD_RECOVERY_REQUIRED: previous build tree has not been confirmed empty')
    atomic_json(receipt.with_name(receipt.stem + '.recovered-' + uuid.uuid4().hex[:8] + '.json'), previous)


def require_receipt(directory, release, sha_full):
    path = Path(directory) / 'builds' / (Path(release).name + '.json')
    if not path.is_file():
        raise RuntimeError('Release requires a successful contained build receipt')
    record = read_json(path)
    if (record.get('schemaVersion') != 1 or record.get('success') is not True
            or record.get('treeEmpty') is not True or record.get('phase') != 'complete'
            or record.get('shaFull') != sha_full or Path(record.get('release', '')).resolve() != Path(release).resolve()):
        raise RuntimeError('Release build receipt is incomplete, failed or identifies a different release')


def run_build(command, *, stdout, env, timeout, receipt: Path, release: Path, sha_full,
              cwd=None, check_alive=None):
    """Return CompletedProcess only after the entire isolated build tree exits.

    check_alive may raise for cancellation/resource pressure. An exception or
    timeout stops only this Job Object; no process is selected by name or port.
    """
    if os.name != 'nt':
        raise RuntimeError('Contained blue-green builds require Windows Job Objects')
    if timeout <= 0 or not re.fullmatch('[0-9a-f]{40}', sha_full):
        raise ValueError('A positive build deadline and full commit SHA are required')
    receipt, release = Path(receipt).resolve(), Path(release).resolve()
    verify_previous(receipt)
    environment = {key: value for key, value in env.items()
                   if not key.startswith(('PPLID_WORKER_', 'PPLID_SUPERVISOR_'))}
    environment.update(PPLID_WEB_ONLY='1', BI_SYNC_SCHEDULER_AUTOSTART='False')
    if check_alive:
        check_alive()
    job = _BuildJob()
    record = {'schemaVersion': 1, 'phase': 'building', 'success': False, 'treeEmpty': False,
              'shaFull': sha_full, 'release': str(release), 'jobName': job.name,
              'generation': uuid.uuid4().hex, 'owner': processes.identity(os.getpid()), 'startedAt': utc()}
    process = None
    outcome = None
    try:
        atomic_json(receipt, record)
        process = _BuildProcess(job, command, stdout, environment, cwd)
        record['process'] = processes.identity(process.pid)
        # Publish the PID/assigned intent before any child instruction executes.
        atomic_json(receipt, record)
        process.resume()
        deadline = time.monotonic() + timeout
        next_check = 0
        while True:
            code = process.poll()
            if code is not None and job.active() == 0:
                outcome = subprocess.CompletedProcess(command, code)
                break
            if code is not None and code != 0:
                # A failed parent may leave npm/pip descendants behind.
                outcome = subprocess.CompletedProcess(command, code)
                break
            now = time.monotonic()
            if now >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            if check_alive and now >= next_check:
                check_alive()
                next_check = now + 1
            time.sleep(.05)
    except BaseException as error:
        record['error'] = type(error).__name__
        raise
    finally:
        try:
            # The native constructor can fail after CreateProcess. Atomic
            # JOB_LIST containment still lets us account for/stop that child.
            if job.active():
                job.terminate()
            deadline = time.monotonic() + 30
            while job.active():
                if time.monotonic() >= deadline:
                    raise RuntimeError('BUILD_RECOVERY_REQUIRED: build Job Object did not become empty')
                time.sleep(.05)
            if process:
                process.wait(timeout=10)
            record.update(treeEmpty=True, phase='complete' if outcome and outcome.returncode == 0 else 'failed',
                          success=bool(outcome and outcome.returncode == 0), finishedAt=utc())
            if outcome:
                record['exitCode'] = outcome.returncode
        except BaseException as error:
            record.update(phase='recovery-required', success=False, treeEmpty=False,
                          cleanupError=type(error).__name__, finishedAt=utc())
            raise RuntimeError('BUILD_RECOVERY_REQUIRED: build shutdown is unconfirmed; receipt preserved') from error
        finally:
            # The handle is non-inherited; closing it also covers failures in
            # receipt persistence. Business worker jobs use a separate module.
            job.close()
            if process:
                process.close()
            atomic_json(receipt, record)
    return outcome
