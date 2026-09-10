"""Testes offline da task Irregularidade GED."""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.bots.rotina.constants import IRREGULARIDADE_COLUNAS_SAIDA, TASK_IRREGULARIDADE
from app.bots.rotina.tasks import TASK_REGISTRY
from app.bots.rotina.tasks import irregularidade as irr_mod
from app.bots.ged import irregularidade_download as ged_mod
from app.bots.rotina.tasks.irregularidade import (
    QuinzenaIrregularidade,
    _aguardar_download_csv,
    _aguardar_progresso_100,
    _fechar_guias_protocolo_ged,
    _limpar_temporarios_orfaos,
    _montar_nome_saida_quinzena,
    _quinzenas_para_atualizar,
    _salvar_csv_tratado,
    _trocar_para_guia_protocolo,
    _voltar_para_guia_principal_ged,
    tratar_irregularidade,
)

_PROTO = "https://ged-web-frontend.claro.br.experian.eeco/protocolo"
_REL = "https://ged-web-frontend.claro.br.experian.eeco/relatorio/posvenda/relirregpos"


class _FakeGedDriver:
    """Driver mínimo para testes de troca de abas GED."""

    def __init__(self, urls: dict[str, str], current: str | None = None):
        self.handles = list(urls.keys())
        self.urls = dict(urls)
        self.current = current or self.handles[0]
        self.closed: list[str] = []
        self.switch_to = type("sw", (), {"window": self.switch_to_window})()

    @property
    def window_handles(self):
        return list(self.handles)

    @property
    def current_window_handle(self):
        return self.current

    def switch_to_window(self, handle):
        if handle not in self.handles:
            raise RuntimeError(f"handle fechado: {handle}")
        self.current = handle

    @property
    def current_url(self):
        return self.urls.get(self.current, "")

    def close(self):
        if self.current in self.handles:
            self.closed.append(self.current)
            self.handles.remove(self.current)
            del self.urls[self.current]
            self.current = self.handles[0] if self.handles else ""


def _df_origem_minimo() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Protocolo": ["123", "456"],
            "Status Contrato": ["Ativo", "Ativo"],
            "MSISDN": ["11999999999", "11888888888"],
            "CPF": ["12345678901", "98765432100"],
            "Data Recebimento": ["01/06/2026", "01/06/2026"],
            "Data da contestação": ["02/06/2026", "02/06/2026"],
            "Data de resposta": ["03/06/2026", "03/06/2026"],
            "Tipo de serviço": ["Movel", "Movel"],
            "Regional": ["SP", "RJ"],
            "Estado": ["SP", "RJ"],
            "Canal de Ativação": ["Loja", "Loja"],
            "Cód. PDV": ["001", "002"],
            "Usuario": ["94192348", "94192349"],
            "Status Contestação": ["Aberta", "Fechada"],
            "Matricula do Inspetor": ["94190000", "94190001"],
            "Descrição das Irregularidades": ["CO - Fraude", "IC - Inconsistencia"],
        }
    )


def test_tratar_irregularidade_renomeia_colunas():
    df = tratar_irregularidade(_df_origem_minimo())
    assert list(df.columns) == IRREGULARIDADE_COLUNAS_SAIDA
    assert "Usuário" in df.columns
    assert "Data do Recebimento" in df.columns


def test_tratar_irregularidade_remove_prefixos_co_ic():
    df = tratar_irregularidade(_df_origem_minimo())
    valores = df["Descrição das Irregularidades"].tolist()
    assert valores[0] == "Fraude"
    assert valores[1] == "Inconsistencia"


def test_tratar_irregularidade_coluna_ausente():
    df = _df_origem_minimo().drop(columns=["Protocolo"])
    with pytest.raises(ValueError, match="Colunas obrigatórias ausentes"):
        tratar_irregularidade(df)


def test_eh_arquivo_irregularidade_sem_extensao():
    from app.bots.rotina.tasks.irregularidade import _eh_arquivo_irregularidade
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "irregularidades_exp_6a29c726cc685"
        caminho.write_text("Protocolo;Status\n1;OK", encoding="utf-8")
        assert _eh_arquivo_irregularidade(caminho) is True


def test_task_irregularidade_no_registry():
    assert TASK_IRREGULARIDADE in TASK_REGISTRY


def test_montar_nome_saida_quinzena():
    assert _montar_nome_saida_quinzena("202606", 1) == "ged-irregularidade-tratado_202606_1.csv"
    assert _montar_nome_saida_quinzena("202605", 2) == "ged-irregularidade-tratado_202605_2.csv"


def test_voltar_para_guia_principal_ged_foca_aba_1():
    drv = _FakeGedDriver(
        {
            "aba1": f"{_REL}",
            "aba2": f"{_PROTO}/123",
        },
        current="aba2",
    )

    _voltar_para_guia_principal_ged(drv)

    assert drv.current == "aba1"
    assert "aba2" in drv.closed
    assert "aba2" not in drv.handles


def test_trocar_para_guia_protocolo_escolhe_mais_recente():
    """Com Q1 e Q2 abertas, deve focar a última guia /protocolo (nova), não a stale."""
    drv = _FakeGedDriver(
        {
            "rel": _REL,
            "proto_q1": f"{_PROTO}/old",
            "proto_q2": f"{_PROTO}/new",
        },
        current="rel",
    )

    _trocar_para_guia_protocolo(drv)

    assert drv.current == "proto_q2"


def test_voltar_nao_remove_protocolo_antes_do_fix_documenta_risco(monkeypatch):
    """Simula cenário Q1→Q2: após voltar sem fechar, ≥2 protocolos deixariam stale no loop antigo."""
    drv = _FakeGedDriver(
        {
            "rel": _REL,
            "proto_q1": f"{_PROTO}/q1",
        },
        current="proto_q1",
    )
    # Snapshot do bug: voltar foca relatório mas deixa protocolo aberto.
    # (Com o fix, _voltar fecha protocolo; este helper isola a contagem pré-fix.)
    monkeypatch.setattr(irr_mod, "_fechar_guias_protocolo_ged", lambda _d: None)
    _voltar_para_guia_principal_ged(drv)
    assert drv.current == "rel"
    assert "proto_q1" in drv.handles

    # Consultar Q2 abre nova guia
    drv.handles.append("proto_q2")
    drv.urls["proto_q2"] = f"{_PROTO}/q2"
    assert sum(1 for h in drv.handles if drv.urls[h].startswith(_PROTO)) >= 2


def test_aguardar_progresso_100_retorna_se_link_ja_presente(monkeypatch):
    class _Link:
        def get_attribute(self, _name):
            return "https://example/download.csv"

    drv = _FakeGedDriver({"proto": f"{_PROTO}/1"}, current="proto")

    monkeypatch.setattr(
        ged_mod,
        "_encontrar_link_download",
        lambda _d, timeout=1: (_Link(), "https://example/download.csv", "//a"),
    )
    monkeypatch.setattr(ged_mod, "wait_for_element", lambda *a, **k: None)

    inicio = time.time()
    _aguardar_progresso_100(drv, timeout=5)
    assert (time.time() - inicio) < 2


def test_repro_q1_q2_ciclo_abas_sem_stale():
    """Reprodução controlada Q1→Q2: fecha protocolo da Q1 e foca só o da Q2."""
    drv = _FakeGedDriver(
        {
            "rel": _REL,
            "proto_q1": f"{_PROTO}/q1",
        },
        current="proto_q1",
    )

    # Fim da Q1: volta e fecha protocolo antigo
    _voltar_para_guia_principal_ged(drv)
    assert drv.handles == ["rel"]
    assert "proto_q1" in drv.closed

    # Consultar Q2 abre nova guia
    drv.handles.append("proto_q2")
    drv.urls["proto_q2"] = f"{_PROTO}/q2"

    _trocar_para_guia_protocolo(drv)
    assert drv.current == "proto_q2"
    protocolos = [h for h in drv.handles if drv.urls[h].startswith(_PROTO)]
    assert protocolos == ["proto_q2"]


def test_bug_legado_primeira_guia_seria_stale():
    """Documenta o critério de hang: com 2 /protocolo, a 1ª handle é a Q1 (stale)."""
    drv = _FakeGedDriver(
        {
            "rel": _REL,
            "proto_q1": f"{_PROTO}/q1",
            "proto_q2": f"{_PROTO}/q2",
        },
        current="rel",
    )
    # Comportamento antigo (primeira match) — não usar em produção
    primeira = None
    for handle in drv.window_handles:
        drv.switch_to.window(handle)
        if drv.current_url.startswith(_PROTO):
            primeira = handle
            break
    assert primeira == "proto_q1"
    # Fix atual escolhe a mais recente
    _trocar_para_guia_protocolo(drv)
    assert drv.current == "proto_q2"


def test_fechar_guias_protocolo_mantem_relatorio():
    drv = _FakeGedDriver(
        {
            "rel": _REL,
            "proto_q1": f"{_PROTO}/q1",
            "proto_q2": f"{_PROTO}/q2",
        },
        current="proto_q2",
    )
    _fechar_guias_protocolo_ged(drv)
    assert drv.handles == ["rel"]
    assert set(drv.closed) == {"proto_q1", "proto_q2"}
    assert drv.current == "rel"


def test_salvar_csv_tratado_espelha_copia_gerencial(monkeypatch, tmp_path):
    pasta_bots = tmp_path / "bots"
    pasta_gerencial = tmp_path / "indicador_qualidade"
    monkeypatch.setattr(irr_mod, "PASTA_GED_IRREGULARIDADE_TRATADO", pasta_bots)
    monkeypatch.setattr(irr_mod, "PASTA_GED_IRREGULARIDADE_COPIA_GERENCIAL", pasta_gerencial)

    quinzena = QuinzenaIrregularidade(
        yyyymm="202606",
        numero=1,
        data_inicio=date(2026, 6, 1),
        data_fim=date(2026, 6, 10),
    )
    caminho = _salvar_csv_tratado(tratar_irregularidade(_df_origem_minimo()), quinzena)

    assert caminho.exists()
    copia = pasta_gerencial / caminho.name
    assert copia.exists()
    assert copia.read_text(encoding="cp1252") == caminho.read_text(encoding="cp1252")


def _assert_quinzena(q: QuinzenaIrregularidade, yyyymm: str, numero: int, inicio: date, fim: date):
    assert q.yyyymm == yyyymm
    assert q.numero == numero
    assert q.data_inicio == inicio
    assert q.data_fim == fim


@pytest.mark.parametrize(
    "data_ref,esperadas",
    [
        (
            date(2026, 6, 10),
            [
                ("202605", 2, date(2026, 5, 16), date(2026, 5, 31)),
                ("202606", 1, date(2026, 6, 1), date(2026, 6, 10)),
            ],
        ),
        (
            date(2026, 6, 20),
            [
                ("202606", 1, date(2026, 6, 1), date(2026, 6, 15)),
                ("202606", 2, date(2026, 6, 16), date(2026, 6, 20)),
            ],
        ),
        (
            date(2026, 3, 9),
            [
                ("202602", 2, date(2026, 2, 16), date(2026, 2, 28)),
                ("202603", 1, date(2026, 3, 1), date(2026, 3, 9)),
            ],
        ),
        (
            date(2026, 1, 5),
            [
                ("202512", 2, date(2025, 12, 16), date(2025, 12, 31)),
                ("202601", 1, date(2026, 1, 1), date(2026, 1, 5)),
            ],
        ),
    ],
)
def test_quinzenas_para_atualizar(data_ref, esperadas):
    quinzenas = _quinzenas_para_atualizar(data_ref)
    assert len(quinzenas) == 2
    for q, (yyyymm, numero, inicio, fim) in zip(quinzenas, esperadas):
        _assert_quinzena(q, yyyymm, numero, inicio, fim)


def _snapshot_vazio(worker: Path, downloads: Path) -> dict[str, set[str]]:
    return {
        str(worker.resolve()): set(),
        str(downloads.resolve()): set(),
    }


def test_aguardar_download_csv_remove_crdownload_orfao_com_csv(monkeypatch, tmp_path):
    worker = tmp_path / "ged_irregularidade"
    downloads = tmp_path / "Downloads"
    worker.mkdir()
    downloads.mkdir()
    monkeypatch.setattr(ged_mod, "DEFAULT_DOWNLOAD", downloads)

    nome_base = "irregularidades_exp_6a2adb38ed4c9"
    csv_file = worker / f"{nome_base}.csv"
    csv_file.write_text("Protocolo;Status\n1;OK", encoding="utf-8")
    crdownload = worker / f"{nome_base}.crdownload"
    crdownload.write_bytes(b"partial")

    resultado = _aguardar_download_csv(
        worker,
        _snapshot_vazio(worker, downloads),
        time.time() - 10,
        timeout=8,
    )

    assert resultado == csv_file
    assert not crdownload.exists()
    assert _limpar_temporarios_orfaos(worker) == 0


def test_aguardar_download_csv_ignora_crdownload_em_downloads(monkeypatch, tmp_path):
    worker = tmp_path / "ged_irregularidade"
    downloads = tmp_path / "Downloads"
    worker.mkdir()
    downloads.mkdir()
    monkeypatch.setattr(ged_mod, "DEFAULT_DOWNLOAD", downloads)

    csv_file = worker / "irregularidades_exp_abc123.csv"
    csv_file.write_text("Protocolo;Status\n1;OK", encoding="utf-8")
    (downloads / "arquivo_antigo.crdownload").write_bytes(b"stuck")

    resultado = _aguardar_download_csv(
        worker,
        _snapshot_vazio(worker, downloads),
        time.time() - 10,
        timeout=8,
    )

    assert resultado == csv_file
    assert (downloads / "arquivo_antigo.crdownload").exists()


def test_aguardar_download_csv_aguarda_crdownload_sem_par_final(monkeypatch, tmp_path):
    worker = tmp_path / "ged_irregularidade"
    downloads = tmp_path / "Downloads"
    worker.mkdir()
    downloads.mkdir()
    monkeypatch.setattr(ged_mod, "DEFAULT_DOWNLOAD", downloads)

    (worker / "irregularidades_exp_pendente.crdownload").write_bytes(b"downloading")

    inicio = time.time()
    with pytest.raises(TimeoutError, match="Timeout aguardando download CSV"):
        _aguardar_download_csv(
            worker,
            _snapshot_vazio(worker, downloads),
            time.time(),
            timeout=2,
        )
    assert (time.time() - inicio) >= 1.5
