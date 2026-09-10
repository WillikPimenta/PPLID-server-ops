"""Pacote rotina — automação de relatórios BRFlow."""

from app.bots.rotina.orchestration import executar_novo_bot, start, stop

__all__ = ["start", "stop", "executar_novo_bot"]
