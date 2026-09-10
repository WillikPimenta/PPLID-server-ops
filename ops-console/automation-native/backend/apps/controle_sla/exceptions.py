class ControleSlaError(Exception):
    """Erro genérico do Controle de SLA."""


class AuthFailureError(ControleSlaError):
    """Credenciais inválidas — nunca retentar automaticamente (anti-lockout)."""


class SessionExpiredError(ControleSlaError):
    """Sessão BrFlow/Okta expirada — pode reconectar se houver credenciais em memória."""
