"""Testes de seleção de tarefas e filtro de download no painel BRFlow."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.bots.rotina.constants import TASK_DEFAULT_ALL, TASK_ROTINAS_1d
from app.bots.rotina.orchestration import _parse_tarefas
from app.bots.rotina.selenium_brflow import _encontrar_linhas_rotina, _extrair_sufixo_brbr4467
from app.bots.rotina.tasks.auditoria import _emitir_sync_g_auditoria


class _FakeElement:
    def __init__(self, text: str):
        self._text = text

    def find_element(self, by, xpath):
        return _FakeElement(self._text)

    @property
    def text(self):
        return self._text


class _FakeDriver:
    def __init__(self, rows_by_xpath: dict[str, list[_FakeElement]]):
        self._rows_by_xpath = rows_by_xpath
        self.xpaths_consultados: list[str] = []

    def find_elements(self, by, xpath):
        self.xpaths_consultados.append(xpath)
        return list(self._rows_by_xpath.get(xpath, []))


def test_parse_tarefas_none_retorna_padrao():
    assert _parse_tarefas({}) == list(TASK_DEFAULT_ALL)
    assert _parse_tarefas({"tarefas": None}) == list(TASK_DEFAULT_ALL)
    assert _parse_tarefas(None) == list(TASK_DEFAULT_ALL)


def test_parse_tarefas_lista_vazia_nao_retorna_padrao():
    assert _parse_tarefas({"tarefas": []}) == []


def test_parse_tarefas_string_todas():
    assert _parse_tarefas({"tarefas": "todas"}) == list(TASK_DEFAULT_ALL)


def test_parse_tarefas_ids_validos():
    assert _parse_tarefas({"tarefas": ["rotinas_1d", "produtividade_d1"]}) == [
        "rotinas_1d",
        "produtividade_d1",
    ]


def test_parse_tarefas_ids_invalidos_retorna_vazio():
    assert _parse_tarefas({"tarefas": ["foo", "bar"]}) == []


def test_extrair_sufixo_brbr4467():
    desc = "BRBR-4467 - Detalhado de registros Todos os clientes 1 Dia atrás"
    assert _extrair_sufixo_brbr4467(desc) == "1 Dia atrás"
    assert _extrair_sufixo_brbr4467("BRBR-5336 > Detalhado de Produtividade D-1") is None


def test_encontrar_linhas_rotina_match_parcial_td2():
    desc = "BRBR-4467 - Detalhado de registros Todos os clientes 1 Dia atrás"
    xpath = f"//tr[contains(td[2], '{desc}')]"
    drv = _FakeDriver({xpath: [_FakeElement(desc)]})

    rows = _encontrar_linhas_rotina(drv, desc, correspondencia_exata=False)

    assert len(rows) == 1
    assert xpath in drv.xpaths_consultados
    assert "//tr[contains(., 'BRBR-4467')]" not in drv.xpaths_consultados


def test_encontrar_linhas_rotina_fallback_sufixo_sem_fallback_generico():
    desc = "BRBR-4467 - Detalhado de registros Todos os clientes 1 Dia atrás"
    xpath_desc = f"//tr[contains(td[2], '{desc}')]"
    xpath_sufixo = "//tr[contains(td[2], '1 Dia atrás')]"
    drv = _FakeDriver({
        xpath_desc: [],
        xpath_sufixo: [_FakeElement(desc)],
    })

    rows = _encontrar_linhas_rotina(drv, desc, correspondencia_exata=False)

    assert len(rows) == 1
    assert xpath_sufixo in drv.xpaths_consultados
    assert "//tr[contains(., 'BRBR-4467')]" not in drv.xpaths_consultados


def test_encontrar_linhas_rotina_sem_match_nao_usa_fallback_brbr4467():
    desc = "BRBR-4467 - Detalhado de registros Todos os clientes 1 Dia atrás"
    xpath_desc = f"//tr[contains(td[2], '{desc}')]"
    xpath_sufixo = "//tr[contains(td[2], '1 Dia atrás')]"
    drv = _FakeDriver({
        xpath_desc: [],
        xpath_sufixo: [],
        "//tr[td[2]]": [_FakeElement("Outra rotina qualquer")],
    })

    rows = _encontrar_linhas_rotina(drv, desc, correspondencia_exata=False)

    assert rows == []
    assert "//tr[contains(., 'BRBR-4467')]" not in drv.xpaths_consultados


def test_encontrar_linhas_rotina_exata():
    desc = "G Auditoria com etapas"
    xpath = f"//tr[normalize-space(td[2])='{desc}']"
    drv = _FakeDriver({xpath: [_FakeElement(desc)]})

    rows = _encontrar_linhas_rotina(drv, desc, correspondencia_exata=True)

    assert len(rows) == 1


def test_emitir_sync_g_auditoria_so_para_parquet_nao_vazio(tmp_path, capsys):
    vazio = tmp_path / "brflow-gauditoria_tratado_20260801.parquet"
    vazio.write_bytes(b"")
    assert _emitir_sync_g_auditoria(vazio) is False
    assert "ROTINA_BRUTO_SAVED" not in capsys.readouterr().out

    valido = tmp_path / "brflow-gauditoria_tratado_20260802.parquet"
    valido.write_bytes(b"parquet")
    assert _emitir_sync_g_auditoria(valido) is True
    assert (
        f"ROTINA_BRUTO_SAVED|g_auditoria|{valido.resolve()}"
        in capsys.readouterr().out
    )


def test_robot_runner_tarefas_sem_env_omite_chave(monkeypatch):
    from app.orchestration import robot_runner

    monkeypatch.delenv("ROBOT_TAREFAS", raising=False)
    settings = robot_runner._build_settings("rotina")
    assert "tarefas" not in settings


def test_robot_runner_tarefas_env_vazia_lista_vazia(monkeypatch):
    from app.orchestration import robot_runner

    monkeypatch.setenv("ROBOT_TAREFAS", "")
    settings = robot_runner._build_settings("rotina")
    assert settings["tarefas"] == []


def test_robot_runner_tarefas_env_com_ids(monkeypatch):
    from app.orchestration import robot_runner

    monkeypatch.setenv("ROBOT_TAREFAS", "rotinas_1d,produtividade_d1")
    settings = robot_runner._build_settings("rotina")
    assert settings["tarefas"] == ["rotinas_1d", "produtividade_d1"]
