"""Account-bound, generation-bound control of the independent Windows executor.

Only framed JSON is transported. DPAPI protects discovery secrets and every
message for this Windows account; credentials are never written to disk. The
bounded deduplication ledger retains encrypted results until process exit and
fails closed when full. A lost response must be retried with the same command ID.
"""
from __future__ import annotations

import base64
import contextvars
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import struct
import threading
import time
import uuid

import psutil

MAX_FRAME = 1024 * 1024
MAX_CLIENTS = 16
MAX_COMMANDS = 10000
TIMEOUT = 3.0
_request_id = contextvars.ContextVar("supervisor_command_id", default=None)
_generation = contextvars.ContextVar("supervisor_generation", default=None)

METHODS = {
    "robots": frozenset("status credentials_status robot_configs update_robot_config start stop stop_all logs clear_logs execution_trends open_folder log_file_path start_okta_credentials_validation cancel_okta_credentials_validation reveal_falhas_process trigger_prioridades_nh validar_plano_replicacao listar_runs_replicacao listar_workflows_replicacao validar_plano_replicacao_d1 listar_runs_replicacao_d1 obter_meta_mensal_replicacao_d1 recalcular_meta_mensal_replicacao_d1 ajustar_meta_mensal_replicacao_d1 limpar_planos_replicacao_d1 start_suporte_claro_jira suporte_claro_jira_status cancel_suporte_claro_jira _normalize_robot_config _append_log".split()),
    "sla": frozenset("status connect disconnect cancel set_gap_ativo set_poll_seconds set_gap_seconds set_farol_thresholds set_protocolos_dias".split()),
    "sla_bridge": frozenset("start_controle_sla stop_controle_sla apply_controle_sla_config".split()),
}
READS = frozenset("status credentials_status robot_configs logs execution_trends log_file_path listar_runs_replicacao listar_runs_replicacao_d1 suporte_claro_jira_status _normalize_robot_config".split())


class ControlError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("Executor: " + code)


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise ControlError("windows_account_required")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    # No UI and no machine-wide flag: only the current user can decrypt.
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), "PPLID supervisor", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ControlError("account_authentication_failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _identity(pid):
    try:
        process = psutil.Process(pid)
        if process.username().casefold() != psutil.Process().username().casefold():
            raise ControlError("wrong_account")
        return process.create_time()
    except (psutil.Error, ValueError, TypeError):
        raise ControlError("unavailable") from None


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ControlError("timeout_outcome_unknown")
    return remaining


def _receive(sock, deadline):
    def read(size):
        data = bytearray()
        while len(data) < size:
            sock.settimeout(_remaining(deadline))
            part = sock.recv(size - len(data))
            if not part:
                raise ControlError("unavailable_outcome_unknown")
            data.extend(part)
        return bytes(data)
    size = struct.unpack("!I", read(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ControlError("invalid_payload")
    return read(size)


def _send(sock, data, deadline):
    if len(data) > MAX_FRAME:
        raise ControlError("payload_too_large")
    sock.settimeout(_remaining(deadline))
    sock.sendall(struct.pack("!I", len(data)) + data)


def remote_enabled():
    # A supervised web process must fail closed if discovery is absent.
    return os.getenv("PPLID_WEB_ONLY") == "1" and bool(os.getenv("PPLID_WORKER_CONTROL_DIR"))


class CommandIdentityMiddleware:
    """Scope explicit HTTP retry IDs to the authenticated actor and endpoint."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get("X-Pplid-Command-Id", "")
        # A caller can replay only their own request on the same endpoint. Do
        # not trust arbitrary serialized objects or caller-selected actor IDs.
        identity = supplied if supplied and len(supplied) <= 80 and supplied.isascii() else uuid.uuid4().hex
        actor = getattr(getattr(request, "user", None), "pk", None)
        actor = str(actor) if actor is not None else None
        scoped = hashlib.sha256(_json([actor, request.method, request.path, identity])).hexdigest()
        marker = _request_id.set(scoped)
        generation_marker = _generation.set(request.headers.get("X-Pplid-Executor-Generation") or None)
        try:
            response = self.get_response(request)
            response["X-Pplid-Command-Id"] = identity
            if _generation.get():
                response["X-Pplid-Executor-Generation"] = _generation.get()
            return response
        finally:
            _request_id.reset(marker)
            _generation.reset(generation_marker)


class RemoteService:
    def __init__(self, service, *, path=None, environment=None, timeout=TIMEOUT):
        self.service = service
        self.path = path or os.getenv("PPLID_SUPERVISOR_CONTROL_FILE")
        self.environment = environment or os.getenv("PPLID_ENVIRONMENT")
        self.timeout = timeout

    def __getattr__(self, method):
        if method not in METHODS[self.service]:
            raise AttributeError(method)
        def call(*args, **kwargs):
            # Model instances never cross the wire. Rehydrate the audit actor in
            # the executor, after the ordinary HTTP permission checks.
            if "user" in kwargs:
                actor = kwargs.pop("user")
                actor_id = getattr(actor, "pk", None)
                kwargs["_actor_id"] = str(actor_id) if actor_id is not None else None
            key = _request_id.get()
            result = self.call(method, args, kwargs, command_id=(key + ":" + self.service + ":" + method) if key else None)
            return Path(result) if method == "log_file_path" and result is not None else result
        return call

    def call(self, method, args=(), kwargs=None, *, command_id=None):
        deadline = time.monotonic() + self.timeout
        try:
            if not self.path:
                raise ControlError("unavailable")
            descriptor = json.loads(Path(self.path).read_text(encoding="utf-8"))
            if descriptor["environment"] != self.environment:
                raise ControlError("wrong_environment")
            if _generation.get() and _generation.get() != descriptor["generation"]:
                raise ControlError("wrong_generation")
            if _request_id.get():
                _generation.set(descriptor["generation"])
            if _identity(descriptor["pid"]) != descriptor["created"]:
                raise ControlError("wrong_generation")
            token = _protect(base64.b64decode(descriptor["key"]), decrypt=True).decode()
            request = {"service": self.service, "method": method, "args": args,
                       "kwargs": kwargs or {}, "id": command_id or uuid.uuid4().hex,
                       "generation": descriptor["generation"], "environment": self.environment,
                       "pid": descriptor["pid"], "created": descriptor["created"], "token": token}
            plaintext = _json(request)
            if len(plaintext) > MAX_FRAME - 4096:
                raise ControlError("payload_too_large")
            encrypted = _protect(plaintext)
            with socket.create_connection(("127.0.0.1", descriptor["port"]), timeout=_remaining(deadline)) as sock:
                _send(sock, encrypted, deadline)
                reply = json.loads(_protect(_receive(sock, deadline), decrypt=True))
            _remaining(deadline)
            if (_identity(descriptor["pid"]) != descriptor["created"] or
                    reply.get("generation") != descriptor["generation"] or reply.get("id") != request["id"]):
                raise ControlError("wrong_generation")
            if "error" in reply:
                raise ControlError(reply["error"])
            return reply["result"]
        except ControlError:
            raise
        except (TimeoutError, socket.timeout):
            raise ControlError("timeout_outcome_unknown") from None
        except (OSError, ValueError, KeyError, TypeError):
            raise ControlError("unavailable_outcome_unknown") from None


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False

    def process_request(self, request, address):
        if not self.owner.clients.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.owner.clients.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.owner.clients.release()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            deadline = time.monotonic() + TIMEOUT
            request = json.loads(_protect(_receive(self.request, deadline), decrypt=True))
            response = self.server.owner.dispatch(request)
            _send(self.request, response, time.monotonic() + TIMEOUT)
        except Exception:
            # Never log request bytes, credentials or exception arguments.
            return


class ControlServer:
    def __init__(self, path, environment, generation, services, draining, *, max_commands=MAX_COMMANDS):
        self.path = Path(path)
        self.environment, self.generation = environment, generation
        self.services, self.draining = services, draining
        self.pid, self.created = os.getpid(), _identity(os.getpid())
        self.token = secrets.token_hex(32)
        self.clients = threading.BoundedSemaphore(MAX_CLIENTS)
        self.lock = threading.Lock()
        self.ledger = {}
        self.max_commands = max_commands
        self.closed = False
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.owner = self

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = {"schema": 1, "environment": self.environment, "generation": self.generation,
                      "pid": self.pid, "created": self.created, "port": self.server.server_address[1],
                      "key": base64.b64encode(_protect(self.token.encode())).decode()}
        temporary = self.path.with_name(self.path.name + "." + self.generation + ".tmp")
        temporary.write_text(json.dumps(descriptor), encoding="utf-8")
        os.replace(temporary, self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def dispatch(self, request):
        request_id = request.get("id")
        def response(**payload):
            return _protect(_json({"generation": self.generation, "id": request_id, **payload}))
        if not hmac.compare_digest(str(request.get("token", "")), self.token):
            return response(error="unauthorized")
        if request.get("environment") != self.environment:
            return response(error="wrong_environment")
        if (request.get("generation") != self.generation or request.get("pid") != self.pid or
                request.get("created") != self.created):
            return response(error="wrong_generation")
        service, method = request.get("service"), request.get("method")
        if (service not in METHODS or method not in METHODS[service] or not isinstance(request_id, str)
                or not 1 <= len(request_id) <= 200 or not isinstance(request.get("args"), list)
                or not isinstance(request.get("kwargs"), dict)):
            return response(error="invalid_command")
        if not self.lock.acquire(blocking=False):
            return response(error="busy")
        try:
            fingerprint = hashlib.sha256(_json([service, method, request["args"], request["kwargs"]])).digest()
            if request_id in self.ledger:
                old_hash, cached = self.ledger[request_id]
                return cached if hmac.compare_digest(old_hash, fingerprint) else response(error="command_id_conflict")
            mutation = method not in READS
            if mutation and (self.closed or self.draining()):
                return response(error="draining")
            if mutation and len(self.ledger) >= self.max_commands:
                return response(error="command_ledger_full")
            try:
                kwargs = dict(request["kwargs"])
                if "_actor_id" in kwargs:
                    if service != "robots" or method != "update_robot_config":
                        raise ControlError("invalid_command")
                    from django.contrib.auth import get_user_model
                    actor_id = kwargs.pop("_actor_id")
                    kwargs["user"] = get_user_model().objects.get(pk=actor_id) if actor_id else None
                result = getattr(self.services[service], method)(*request["args"], **kwargs)
                if method == "log_file_path" and result is not None:
                    result = str(result)
                reply = response(result=result)
            except ControlError as exc:
                reply = response(error=exc.code)
            except Exception:
                reply = response(error="executor_operation_failed")
            if mutation:
                self.ledger[request_id] = (fingerprint, reply)
            return reply
        finally:
            self.lock.release()

    def close(self):
        self.closed = True
        self.server.shutdown()
        self.server.server_close()
        # Accepted synchronous commands are work too; do not let process exit
        # discard one after a caller timed out waiting for its response.
        with self.lock:
            pass
        try:
            if json.loads(self.path.read_text())["generation"] == self.generation:
                self.path.unlink()
        except (OSError, ValueError, KeyError):
            pass


def start_executor_control(path, environment, generation, draining):
    from apps.automacoes.services import get_robot_manager
    from apps.controle_sla.services.poller import get_poller
    from apps.automacoes import controle_sla_bridge
    return ControlServer(path, environment, generation,
                         {"robots": get_robot_manager(), "sla": get_poller(), "sla_bridge": controle_sla_bridge},
                         draining).start()
