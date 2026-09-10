"""Extração GED irregularidade bruta no ciclo Produtividade H/H → fila reinspeção."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import (
    PASTA_GED_IRREGULARIDADE_HXH_BRUTO,
    PLAN_IDF_SERASA_BOTS,
    PREFIXO_GED_IRREGULARIDADE_HXH_BRUTO,
)
from app.core.common import safe_close_driver
from app.infrastructure.bot_sync_drop import (
    DOMAIN_REINSPECAO_GED,
    append_marker_to_robot_log,
    write_sync_drop,
)
from app.bots.ged.irregularidade_download import (
    GedIrregularidadeCallbacks,
    QuinzenaIrregularidade,
    criar_driver_ged,
    descricao_quinzena,
    download_quinzena_irregularidade,
    limpar_pasta_worker,
    login_ged,
)

log = logging.getLogger("robots.bot_production")

DOWNLOADS_TEMP_GED_IRREGULARIDADE_HXH = PLAN_IDF_SERASA_BOTS / "downloads_temp" / "ged_irregularidade_hxh"

PRODUCTION_GED_IRREGULARIDADE_SAVED_PREFIX = "PRODUCTION_GED_IRREGULARIDADE_SAVED|"
PRODUCTION_GED_IRREGULARIDADE_DIAS_RETROATIVOS = 10
_TZ_SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def _cfg_executar_ged_irregularidade(settings: dict | None = None) -> bool:
    if settings is not None and "executar_ged_irregularidade" in settings:
        return bool(settings.get("executar_ged_irregularidade"))
    raw = (os.getenv("PRODUCTION_EXECUTAR_GED_IRREGULARIDADE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _periodo_hxh_irregularidade(*, data_base: date | None = None) -> QuinzenaIrregularidade:
    """Período exclusivo do H/H: D-10 até D0, inclusive, em São Paulo."""
    data_fim = data_base or datetime.now(_TZ_SAO_PAULO).date()
    data_inicio = data_fim - timedelta(days=PRODUCTION_GED_IRREGULARIDADE_DIAS_RETROATIVOS)
    return QuinzenaIrregularidade(
        yyyymm=f"{data_inicio:%Y%m%d}-{data_fim:%Y%m%d}",
        numero=0,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )


def _sha256_arquivo(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handler:
        for chunk in iter(lambda: handler.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _montar_nome_bruto_periodo(
    periodo: QuinzenaIrregularidade,
    *,
    executado_em: datetime,
    content_sha256: str,
    token: str,
) -> str:
    timestamp = executado_em.astimezone(_TZ_SAO_PAULO).strftime("%Y%m%dT%H%M%S%f")
    return (
        f"{PREFIXO_GED_IRREGULARIDADE_HXH_BRUTO}"
        f"{periodo.data_inicio:%Y%m%d}_{periodo.data_fim:%Y%m%d}_"
        f"{timestamp}_{content_sha256[:12]}_{token}.csv"
    )


def _emit_production_ged_irregularidade_saved(
    path: Path,
    *,
    metadata: dict | None = None,
) -> None:
    resolved = path.resolve()
    line = f"{PRODUCTION_GED_IRREGULARIDADE_SAVED_PREFIX}{resolved}"
    printed = False
    try:
        print(line, flush=True)
        printed = True
    except OSError as exc:
        log.warning(
            "Falha ao emitir PRODUCTION_GED_IRREGULARIDADE_SAVED via stdout: %s | path=%s",
            exc,
            resolved,
        )

    write_sync_drop(DOMAIN_REINSPECAO_GED, resolved, extra=metadata)
    append_marker_to_robot_log("production", line)

    if not printed:
        log.info("GED irregularidade H/H: sync via fallback sync_drop (%s)", resolved)


def _salvar_csv_bruto(
    arquivo_baixado: Path,
    periodo: QuinzenaIrregularidade,
    *,
    executado_em: datetime | None = None,
) -> Path:
    """Publica artefato imutável e só então emite o marker de sincronização."""
    PASTA_GED_IRREGULARIDADE_HXH_BRUTO.mkdir(parents=True, exist_ok=True)
    executado_em = executado_em or datetime.now(_TZ_SAO_PAULO)
    content_sha256 = _sha256_arquivo(arquivo_baixado)
    token = uuid.uuid4().hex[:8]
    destino = PASTA_GED_IRREGULARIDADE_HXH_BRUTO / _montar_nome_bruto_periodo(
        periodo,
        executado_em=executado_em,
        content_sha256=content_sha256,
        token=token,
    )
    temporario = destino.with_suffix(destino.suffix + ".tmp")
    try:
        shutil.copy2(str(arquivo_baixado), str(temporario))
        os.replace(temporario, destino)
    finally:
        temporario.unlink(missing_ok=True)

    metadata = {
        "source": "bot_production",
        "period_start": periodo.data_inicio.isoformat(),
        "period_end": periodo.data_fim.isoformat(),
        "generated_at": executado_em.astimezone(_TZ_SAO_PAULO).isoformat(),
        "content_sha256": content_sha256,
        "content_size": destino.stat().st_size,
    }
    log.info(
        "Irregularidade GED bruta H/H salva | periodo=%s..%s | sha256=%s | destino=%s",
        periodo.data_inicio,
        periodo.data_fim,
        content_sha256,
        destino,
    )
    _emit_production_ged_irregularidade_saved(destino, metadata=metadata)
    return destino


def executar_extracao_ged_irregularidade(
    *,
    set_status,
    set_progress=None,
    registrar_erro=None,
    registrar_status=None,
    settings: dict | None = None,
    data_base: date | None = None,
) -> bool:
    """Baixa D-10..D0 do GED e emite artefato bruto imutável para reinspeção."""
    if not _cfg_executar_ged_irregularidade(settings):
        set_status("Producao: GED irregularidade desabilitado neste ciclo")
        return True

    cb = GedIrregularidadeCallbacks(set_status=set_status, set_progress=set_progress)
    periodo = _periodo_hxh_irregularidade(data_base=data_base)
    pasta_temp = DOWNLOADS_TEMP_GED_IRREGULARIDADE_HXH
    pasta_temp.mkdir(parents=True, exist_ok=True)

    ged_drv = None

    try:
        set_status(
            "Producao: extraindo GED irregularidade "
            f"({periodo.data_inicio:%d/%m/%Y} a {periodo.data_fim:%d/%m/%Y})"
        )
        if set_progress:
            set_progress(93, "Producao: GED irregularidade")

        limpar_pasta_worker(pasta_temp)
        ged_drv = criar_driver_ged(pasta_temp)
        login_ged(ged_drv, cb)

        arquivo = download_quinzena_irregularidade(
            ged_drv,
            pasta_temp,
            periodo,
            1,
            1,
            cb,
            progress_base=93,
            progress_span=5,
        )
        if not arquivo:
            msg = f"GED irregularidade: relatório vazio ({descricao_quinzena(periodo)})"
            log.warning(msg)
            if registrar_erro:
                registrar_erro(msg)
            if registrar_status:
                registrar_status("ged_irregularidade", False, "relatório vazio")
            set_status("Producao: GED irregularidade — nenhum arquivo salvo")
            return False

        destino = _salvar_csv_bruto(arquivo, periodo)
        if registrar_status:
            registrar_status("ged_irregularidade", True, "")
        set_status(f"Producao: GED irregularidade salva — {destino.name}")
        return True

    except Exception as exc:
        log.exception("Falha na extração GED irregularidade H/H")
        if registrar_erro:
            registrar_erro(f"GED irregularidade: {str(exc)[:120]}")
        if registrar_status:
            registrar_status("ged_irregularidade", False, str(exc)[:30])
        set_status(f"Producao: GED irregularidade falhou — {str(exc)[:100]}")
        return False

    finally:
        safe_close_driver(ged_drv, logger=log)
