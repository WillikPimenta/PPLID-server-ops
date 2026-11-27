"""Teams recovery state. SQLite is local and independent of Portal/Postgres."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path


class RecoveryError(Exception):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class RecoveryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS code_requests (id TEXT PRIMARY KEY, browser TEXT, code TEXT, username TEXT, ip TEXT, created REAL, expires REAL, state TEXT, attempts INTEGER, session_until REAL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def code_digest(browser, code):
        return hmac.new(browser.encode(), code.encode(), hashlib.sha256).hexdigest()

    def create(self, username, ip):
        now = time.time()
        browser, ident = secrets.token_urlsafe(32), secrets.token_hex(16)
        code = f"{secrets.randbelow(100000000):08d}"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM code_requests WHERE created < ?", (now - 86400,))
            if db.execute("SELECT COUNT(*) FROM code_requests WHERE created > ?", (now - 600,)).fetchone()[0] >= 10:
                raise RecoveryError("Muitas solicitacoes. Aguarde alguns minutos.")
            if db.execute("SELECT 1 FROM code_requests WHERE ip = ? AND created > ?", (ip, now - 60)).fetchone():
                raise RecoveryError("Aguarde um minuto antes de solicitar novamente.")
            db.execute("INSERT INTO code_requests VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0)",
                       (ident, digest(browser), self.code_digest(browser, code), username, ip, now, now + 600))
        return {"id": ident, "browserToken": browser, "code": code}

    def consume(self, ident, browser, code):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM code_requests WHERE id = ?", (ident,)).fetchone()
            if not row or not secrets.compare_digest(row["browser"], digest(browser)):
                raise RecoveryError("Solicitacao invalida")
            if row["expires"] <= now or row["state"] != "pending":
                raise RecoveryError("Solicitacao encerrada ou expirada")
            if not secrets.compare_digest(row["code"], self.code_digest(browser, code)):
                attempts = row["attempts"] + 1
                db.execute("UPDATE code_requests SET attempts = ?, state = ? WHERE id = ?",
                           (attempts, "locked" if attempts >= 5 else "pending", ident))
                return {"state": "locked" if attempts >= 5 else "invalid"}
            until = now + 1800
            db.execute("UPDATE code_requests SET state = 'consumed', session_until = ? WHERE id = ?", (until, ident))
            return {"state": "approved", "username": row["username"], "expires": until}

    def valid_session(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT session_until FROM code_requests WHERE id = ? AND state = 'consumed'", (ident,)).fetchone()
            return bool(row and row["session_until"] > time.time())

    def cancel(self, ident):
        with self.connect() as db:
            db.execute("UPDATE code_requests SET state = 'cancelled' WHERE id = ?", (ident,))


def send_request(webhook, request, username):
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
        "body": [{"type": "TextBlock", "text": "OPS Console - Recuperacao emergencial", "weight": "Bolder", "wrap": True},
                 {"type": "TextBlock", "text": "Usuario informado (identidade nao validada): " + username, "wrap": True},
                 {"type": "TextBlock", "text": "Solicitacao: " + request["id"], "wrap": True},
                 {"type": "TextBlock", "text": "Codigo: " + request["code"], "weight": "Bolder", "size": "Large", "wrap": True},
                 {"type": "TextBlock", "text": "Valido por 10 minutos, uso unico. Confirme a identidade e o identificador com o solicitante antes de entregar o codigo. Libera somente o navegador solicitante por 30 minutos.", "wrap": True}]}
    payload = {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}
    req = urllib.request.Request(webhook, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.status not in (200, 201, 202, 204):
            raise RecoveryError("Falha no envio ao Teams")
