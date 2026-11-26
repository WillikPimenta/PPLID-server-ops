"""Opt-in, cross-process admission and cooperative worker draining.

No process is terminated here. Unfinished/orphan leases fail closed. Only
supervised deployments set PPLID_WORKER_CONTROL_DIR; legacy execution is unchanged.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import json
import os
from pathlib import Path
import threading
import time
import uuid

_thread_lock = threading.RLock()


class AdmissionClosed(RuntimeError):
    pass


def _identity(pid):
    import psutil
    p = psutil.Process(pid)
    return {'pid': p.pid, 'created': p.create_time(), 'exe': p.exe(), 'cmdline': p.cmdline()}


def _alive(identity):
    import psutil
    try:
        actual = _identity(identity['pid'])
        if actual['created'] != identity['created']:
            return False
        if actual != identity:
            raise RuntimeError('Worker process identity changed; ownership is unconfirmed')
        return True
    except psutil.NoSuchProcess:
        return False


def _write(path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(10):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.03)
    finally:
        temp.unlink(missing_ok=True)


class Lease:
    def __init__(self, controller=None, path=None, token=None):
        self.controller, self.path, self.token = controller, path, token

    def close(self):
        if self.path is None:
            return
        with self.controller.lock():
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value['token'] != self.token:
                raise RuntimeError('Worker lease identity changed; preserving record')
            self.path.unlink()
        self.path = None

    def __enter__(self):
        return self

    def bind_child(self, pid):
        import psutil
        with self.controller.lock():
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value['token'] != self.token:
                raise RuntimeError('Launch intent identity changed')
            value['child'] = {'pid': pid, 'created': psutil.Process(pid).create_time()}
            _write(self.path, value)

    def __exit__(self, *error):
        self.close()


class Controller:
    def __init__(self, root, owner_group=None):
        self.root = Path(root)
        self.owner_group = owner_group
        self.state = self.root / 'state.json'
        self.units = self.root / 'units'

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with _thread_lock, (self.root / 'admission.lock').open('a+b') as stream:
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b'0')
                stream.flush()
            deadline = time.monotonic() + 10
            while True:
                stream.seek(0)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Worker admission lock busy')
                    time.sleep(.02)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def read(self):
        value = json.loads(self.state.read_text(encoding='utf-8'))
        if value.get('schemaVersion') != 1 or value.get('phase') not in ('starting', 'open', 'quiescing', 'stopped'):
            raise RuntimeError('Invalid worker admission state')
        return value

    def initialize(self, instance):
        with self.lock():
            self.units.mkdir(exist_ok=True)
            if list(self.units.glob('*.json')):
                raise RuntimeError('Unfinished worker leases exist; restart refused')
            if self.state.exists() and self.read()['phase'] != 'stopped':
                raise RuntimeError('Previous worker generation is not stopped')
            retired = self.read().get('retiredGroups', []) if self.state.exists() else []
            _write(self.state, {'schemaVersion': 1, 'instanceId': instance, 'phase': 'starting', 'retiredGroups': retired})

    def open(self, instance):
        with self.lock():
            state = self.read()
            if state['instanceId'] != instance or state['phase'] != 'starting':
                raise RuntimeError('Worker generation cannot open admission')
            state['phase'] = 'open'
            state['owner'] = _identity(os.getpid())
            _write(self.state, state)

    def request(self, instance):
        with self.lock():
            state = self.read()
            if state['instanceId'] != instance:
                raise RuntimeError('Worker generation mismatch')
            if state['phase'] != 'stopped':
                state['phase'] = 'quiescing'
                _write(self.state, state)

    def requested(self):
        try:
            state = self.read()
            return (state['phase'] != 'open' or self.owner_group in state.get('retiredGroups', [])
                    or not _alive(state['owner']))
        except Exception:
            return True

    def retire(self, owner_group):
        if not owner_group:
            raise ValueError('A concrete worker owner group is required')
        with self.lock():
            state = self.read()
            state['retiredGroups'] = list(dict.fromkeys(state.get('retiredGroups', []) + [owner_group]))
            _write(self.state, state)

    def reserve(self, kind, *, unique=False):
        with self.lock():
            state = self.read()
            if (state['phase'] != 'open' or self.owner_group in state.get('retiredGroups', [])
                    or not _alive(state['owner'])):
                raise AdmissionClosed('Execuções em manutenção; aguarde a conclusão da drenagem.')
            if unique and any(json.loads(path.read_text(encoding='utf-8'))['kind'] == kind for path in self.units.glob('*.json')):
                raise AdmissionClosed('O serviço já possui um executor registrado.')
            token = uuid.uuid4().hex
            path = self.units / (token + '.json')
            _write(path, {'token': token, 'kind': kind, 'instanceId': state['instanceId'], 'group': self.owner_group,
                          'owner': _identity(os.getpid())})
            return Lease(self, path, token)

    def status(self):
        with self.lock():
            state = self.read()
            units = [json.loads(path.read_text(encoding='utf-8')) for path in self.units.glob('*.json')]
            for value in units:
                if value.get('instanceId') != state['instanceId'] or not value.get('token') or not value.get('owner'):
                    raise RuntimeError('Invalid or foreign worker lease; preserving records')
            return {**state, 'units': [{'kind': u['kind'], 'token': u['token'], 'group': u.get('group'),
                    'ownerAlive': _alive(u['owner'])} for u in units]}

    def consume_inherited(self, token):
        """Claim already admitted work, including when quiescing won the race
        against interpreter startup. The capability is single-use and bound
        to the suspended child (or its venv interpreter descendant).
        """
        import psutil
        if len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
            raise AdmissionClosed('Invalid inherited worker admission')
        with self.lock():
            path = self.units / (token + '.json')
            value = json.loads(path.read_text(encoding='utf-8'))
            state = self.read()
            if value['instanceId'] != state['instanceId'] or value.get('group') != self.owner_group:
                raise AdmissionClosed('Inherited worker generation mismatch')
            child = value['child']
            chain = [psutil.Process(), *psutil.Process().parents()]
            if not any(p.pid == child['pid'] and p.create_time() == child['created'] for p in chain):
                raise AdmissionClosed('Inherited worker process ownership mismatch')
            if value.get('claimed'):
                raise AdmissionClosed('Worker admission was already claimed')
            value['claimed'] = _identity(os.getpid())
            _write(path, value)

    def confirm_stopped(self, instance):
        with self.lock():
            state = self.read()
            if state['instanceId'] != instance or state['phase'] not in ('quiescing', 'stopped'):
                raise RuntimeError('Worker generation cannot confirm shutdown')
            if list(self.units.glob('*.json')):
                raise RuntimeError('Work is still running or orphaned; shutdown unconfirmed')
            state['phase'] = 'stopped'
            _write(self.state, state)


def current():
    root = os.environ.get('PPLID_WORKER_CONTROL_DIR')
    return Controller(root, os.environ.get('PPLID_WORKER_OWNER')) if root else None


def requested():
    control = current()
    return control.requested() if control else False


def wait(seconds, event=None):
    """Interrupt idle waiting only. Never set a bot's cancellation event."""
    if current() is None:
        if event is not None:
            return event.wait(seconds)
        time.sleep(seconds)
        return False
    deadline = time.monotonic() + max(0, seconds)
    while time.monotonic() < deadline:
        if requested() or (event is not None and event.is_set()):
            return True
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    return requested() or (event is not None and event.is_set())


def managed(function):
    @wraps(function)
    def run(*args, **kwargs):
        control = current()
        if control is None:
            return function(*args, **kwargs)
        inherited = os.environ.pop('PPLID_WORKER_ADMITTED_TOKEN', None)
        if inherited:
            control.consume_inherited(inherited)
            # The launching lease stays held until the entire Job Object exits.
            return function(*args, **kwargs)
        try:
            lease = control.reserve(function.__module__ + '.' + function.__name__)
        except AdmissionClosed:
            return None
        with lease:
            return function(*args, **kwargs)
    return run


@contextmanager
def cycle(kind):
    control = current()
    if control is None:
        yield True
        return
    try:
        lease = control.reserve(kind)
    except AdmissionClosed:
        yield False
        return
    with lease:
        yield True


def popen(*args, _factory=None, _kind='subprocess', _unique=False, **kwargs):
    """Retain admission until the child and observed descendants exit.

    The launch intent is written BEFORE Popen. If this process dies in that
    window, its lease stays as an explicit blocker; it is never guessed away.
    """
    import subprocess
    control = current()
    factory = _factory or subprocess.Popen
    if control is None:
        return factory(*args, **kwargs)
    if os.name != 'nt':
        raise RuntimeError('Managed subprocess draining requires Windows Job Objects')
    lease = control.reserve(_kind, unique=_unique)
    try:
        group = _WindowsJob()
    except BaseException:
        lease.close()  # No process-creation call has happened.
        raise
    try:
        # Assign a suspended process before any application code can spawn a
        # child. The job has NO kill-on-close / time / memory / UI restrictions.
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x00000004
        environment = dict(kwargs.get('env') or os.environ)
        environment['PPLID_WORKER_ADMITTED_TOKEN'] = lease.token
        kwargs['env'] = environment
        process = factory(*args, **kwargs)
    except BaseException:
        group.close()
        # The factory may have failed after CreateProcess. Keep the launch
        # intent rather than assuming that no suspended child was created.
        raise
    try:
        lease.bind_child(process.pid)
        group.assign_and_resume(process)
    except BaseException:
        if group.resume_attempted:
            group.close()
            raise RuntimeError(f'Child launch state is uncertain (pid={process.pid}); lease preserved')
        # This handle belongs to the suspended process just created here; no
        # business code has run and there is no PID lookup or process-tree kill.
        process.terminate()
        process.wait(timeout=10)
        group.close()
        lease.close()
        raise
    def watch():
        try:
            process.wait()
            # Job accounting includes descendants even if their parent exits
            # between polls. Never infer completion just from the launcher PID.
            while group.active():
                time.sleep(.1)
            lease.close()
        except Exception:
            # Keep the durable intent on any uncertainty, including AccessDenied.
            import logging
            logging.exception('Worker subprocess ownership is unconfirmed; lease preserved')
        finally:
            group.close()
    threading.Thread(target=watch, name='worker-child-lease', daemon=True).start()
    return process


def run_process(*args, _factory=None, _kind='subprocess', _unique=False, **kwargs):
    import subprocess
    if current() is None:
        return subprocess.run(*args, **kwargs)
    timeout = kwargs.pop('timeout', None)
    check = kwargs.pop('check', False)
    process = popen(*args, _factory=_factory, _kind=_kind, _unique=_unique, **kwargs)
    # Unlike subprocess.run(timeout=...), timeout never kills admitted work.
    code = process.wait(timeout=timeout)
    if check and code:
        raise subprocess.CalledProcessError(code, args[0])
    return subprocess.CompletedProcess(args[0], code)


class _WindowsJob:
    """Accounting only: closing this handle never terminates running work."""
    def __init__(self):
        import ctypes
        self.ctypes = ctypes
        k = self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.AssignProcessToJobObject.restype = ctypes.c_int
        k.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        k.QueryInformationJobObject.restype = ctypes.c_int
        k.OpenThread.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        k.OpenThread.restype = ctypes.c_void_p
        k.ResumeThread.argtypes = [ctypes.c_void_p]
        k.ResumeThread.restype = ctypes.c_uint32
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = k.CreateJobObjectW(None, None)
        self.resume_attempted = False
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def assign_and_resume(self, process):
        import psutil
        c, k = self.ctypes, self.kernel
        if not k.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise c.WinError(c.get_last_error())
        threads = psutil.Process(process.pid).threads()
        if len(threads) != 1:
            raise RuntimeError('Suspended child primary thread could not be verified')
        thread = k.OpenThread(0x0002, False, threads[0].id)
        if not thread:
            raise c.WinError(c.get_last_error())
        try:
            self.resume_attempted = True
            previous = k.ResumeThread(thread)
            if previous == 0xFFFFFFFF:
                raise c.WinError(c.get_last_error())
            if previous != 1:
                raise RuntimeError('Unexpected suspend count on unstarted child')
        finally:
            k.CloseHandle(thread)

    def active(self):
        c = self.ctypes
        class Accounting(c.Structure):
            _fields_ = [('user', c.c_longlong), ('kernel', c.c_longlong),
                        ('periodUser', c.c_longlong), ('periodKernel', c.c_longlong),
                        ('faults', c.c_uint32), ('total', c.c_uint32),
                        ('active', c.c_uint32), ('terminated', c.c_uint32)]
        info = Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, c.byref(info), c.sizeof(info), None):
            raise c.WinError(c.get_last_error())
        return info.active

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
