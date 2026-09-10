# -*- coding: utf-8 -*-
"""Exceções operacionais da configuração D-1 no banco."""


class ConfigBancoIndisponivelError(Exception):
    """Banco indisponível ou fonte do banco exigida mas inacessível."""


class ConfigIncompletaError(Exception):
    """Configuração permanente incompleta para planejamento ou ativação."""

    def __init__(self, errors: list[str] | None = None, message: str | None = None):
        self.errors = list(errors or [])
        if message:
            super().__init__(message)
        elif self.errors:
            super().__init__("; ".join(self.errors))
        else:
            super().__init__("Configuração permanente incompleta.")


class ImportPreviewTokenError(Exception):
    """Token de preview inválido, expirado ou divergente."""
