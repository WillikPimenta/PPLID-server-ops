import logging
import os
import json
import requests
from datetime import datetime

from app.config.env import load_local_env

_log = logging.getLogger(__name__)

_CHANNEL_ENV = {
    "Produção": "TEAMS_WEBHOOK_PRODUCAO",
    "Rotina": "TEAMS_WEBHOOK_ROTINA",
    "Alterações": "TEAMS_WEBHOOK_ALTERACOES",
}


def _webhook_url(channel: str) -> str:
    load_local_env()
    env_name = _CHANNEL_ENV.get(channel, "TEAMS_WEBHOOK_DEFAULT")
    return (os.getenv(env_name) or "").strip()


def notify(channel, status: str, title: str, details: str = "", run_id: str = None, adaptive_card: dict = None):
    url = _webhook_url(channel)
    if not url:
        _log.warning(
            "Notificação Teams ignorada [%s]: defina %s em automacoes/.env",
            channel,
            _CHANNEL_ENV.get(channel, "TEAMS_WEBHOOK_DEFAULT"),
        )
        return

    color = {"sucesso": "00B050", "erro": "C00000", "alerta": "FFD965"}.get(status.lower(), "0078D4")
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    quando = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    section = {
        "facts": [
            {"name": "ID da execução", "value": run_id},
            {"name": "Data e hora", "value": quando},
        ],
        "activityImage": "https://www.stand.com.br/blog/wp-content/uploads/2018/02/serasa-experian-logo.png",
    }
    if details:
        section["text"] = details

    status_label = {"sucesso": "Sucesso", "erro": "Pendências", "alerta": "Alerta"}.get(status.lower(), status)
    card_title = title if adaptive_card else f"[{status_label}] {title}"

    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": card_title,
        "title": card_title,
        "sections": [section],
    }

    if adaptive_card:
        card["attachments"] = [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": adaptive_card,
        }]

    try:
        resp = requests.post(
            url,
            data=json.dumps(card),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        _log.warning("Falha ao enviar notificação Teams [%s]: %s", channel, exc)
