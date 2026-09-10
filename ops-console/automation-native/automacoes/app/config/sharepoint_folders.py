"""SharePoint folder links per robot mode — for UI sync/navigation."""

_GESTAO_SLA_BOTS = (
    "https://experian.sharepoint.com/sites/GestodeSLA/Shared%20Documents/Forms/AllItems.aspx"
    "?csf=1&web=1&e=xO2Skc&CID=ed15efcf-0f8d-4240-8e84-03aa284b6893"
    "&FolderCTID=0x0120008E0A83FA04CBF74FB8F87E952701AC32"
    "&id=%2Fsites%2FGestodeSLA%2FShared+Documents%2FBots%2FExtra%C3%A7%C3%A3o+de+usu%C3%A1rios"
)

_PLANEJAMENTO_FALHAS = (
    "https://experian.sharepoint.com/sites/Planejamento-IDF/Shared%20Documents/Forms/AllItems.aspx"
    "?id=%2Fsites%2FPlanejamento%2DIDF%2FShared%20Documents%2FGeneral%2FBases%2FBots%2Freport%2Dfalhas%2Dcriticas"
    "&viewid=8a6f90eb%2Df4ff%2D4fb5%2Db792%2D6faba2e0f808"
)

_PLANEJAMENTO_BASES = (
    "https://experian.sharepoint.com/sites/Planejamento-IDF/Shared%20Documents/Forms/AllItems.aspx"
    "?id=%2Fsites%2FPlanejamento%2DIDF%2FShared%20Documents%2FGeneral%2FBases"
    "&viewid=8a6f90eb%2Df4ff%2D4fb5%2Db792%2D6faba2e0f808"
)

_CONFER = (
    "https://experian.sharepoint.com/:f:/r/sites/OperationFraudSuport/Shared%20Documents/Bots/"
    "Produ%C3%A7%C3%A3o%20-%20Confer?csf=1&web=1&e=ERaNcB"
)


def _link(entry_id: str, url: str) -> dict[str, str]:
    return {"id": entry_id, "label": "", "url": url}


MODE_SHAREPOINT_FOLDERS: dict[str, list[dict[str, str]]] = {
    "nivel": [_link("sharepoint", _GESTAO_SLA_BOTS)],
    "monitor": [_link("sharepoint", _GESTAO_SLA_BOTS)],
    "excel": [_link("sharepoint", _GESTAO_SLA_BOTS)],
    "production": [_link("sharepoint", _PLANEJAMENTO_BASES)],
    "tray_ui": [],
    "rotina": [_link("sharepoint", _PLANEJAMENTO_BASES)],
    "confer": [_link("sharepoint", _CONFER)],
    "ged": [_link("sharepoint", _PLANEJAMENTO_BASES)],
    "replicacao_auditoria": [_link("sharepoint", _PLANEJAMENTO_BASES)],
    "replicacao_auditoria_d1": [_link("sharepoint", _PLANEJAMENTO_BASES)],
    "falhas_criticas": [_link("sharepoint", _PLANEJAMENTO_FALHAS)],
    "produtividade_case": [
        _link("hora", _PLANEJAMENTO_BASES),
        _link("tempo_logado", _PLANEJAMENTO_BASES),
        _link("consolidado", _PLANEJAMENTO_BASES),
    ],
}


def default_sharepoint_url(mode: str) -> str:
    """First folder URL for a mode (backward-compatible single link)."""
    folders = MODE_SHAREPOINT_FOLDERS.get(mode) or []
    if not folders:
        return ""
    return str(folders[0].get("url") or "").strip()


def default_sharepoint_folders(mode: str) -> list[dict[str, str]]:
    """Copy of default folder list for a robot mode."""
    return [dict(item) for item in (MODE_SHAREPOINT_FOLDERS.get(mode) or [])]
