# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pandas as pd

from app.infrastructure.brflow_monitor import (
    MONITOR_EVENTOS_SAVED_PREFIX,
    adaptar_prod_hxh_para_rotina,
    processar_e_salvar_monitor_eventos_tratado,
    unificar_monitor_hxh_com_confer_log,
)


def test_adaptar_prod_hxh_para_rotina_maps_columns_and_filters_day():
    with TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "relatorio_produtividade_consolidado.csv"
        pd.DataFrame(
            [
                {
                    "Matrícula": "C92928A",
                    "Data de Análise": "18/06/2026",
                    "Hora": 10,
                    "Tempo Total": 1800,
                },
                {
                    "Matrícula": "c97960a",
                    "Data de Análise": "17/06/2026",
                    "Hora": 9,
                    "Tempo Total": 900,
                },
            ]
        ).to_csv(csv_path, index=False, sep=";")

        result = adaptar_prod_hxh_para_rotina(csv_path, date(2026, 6, 18), temp_dir=tmp)
        assert result is not None
        df = pd.read_parquet(result)
        assert list(df.columns) == ["desMatricula", "datAnalise", "numTempoAnalise"]
        assert len(df) == 1
        assert df.iloc[0]["desMatricula"] == "c92928a"


@patch("app.bots.rotina.tasks.monitor.tratar_arquivo")
@patch("app.bots.rotina.tasks.monitor._pretratar_monitor_verifica_usuario", return_value=True)
def test_processar_e_salvar_monitor_eventos_tratado_emits_hook(mock_pre, mock_tratar, capsys):
    with TemporaryDirectory() as tmp:
        monitor_csv = Path(tmp) / "relatorio_detalhado.csv"
        prod_csv = Path(tmp) / "relatorio_produtividade_consolidado.csv"
        tratado_rotina = Path(tmp) / "brflow-monitor-tratado_20260618.parquet"

        pd.DataFrame(
            [
                {
                    "Usuário": "c92928a",
                    "Data do Evento": "18/06/2026 10:00:00",
                    "Evento": "Autenticação com sucesso",
                    "ID Sessão": "sess-1",
                    "Objeto": "Login",
                }
            ]
        ).to_csv(monitor_csv, index=False, sep=";")
        pd.DataFrame(
            [
                {
                    "Matrícula": "c92928a",
                    "Data de Análise": "18/06/2026",
                    "Hora": 10,
                    "Tempo Total": 1800,
                }
            ]
        ).to_csv(prod_csv, index=False, sep=";")
        pd.DataFrame(
            [
                {
                    "Data": date(2026, 6, 18),
                    "Hora": 10,
                    "Usuário": "c92928a",
                    "Data do Evento": pd.Timestamp("2026-06-18 10:00:00"),
                    "Evento": "Autenticação com sucesso",
                    "Data segundo evento": pd.Timestamp("2026-06-18 12:00:00"),
                    "Segundo evento": "Logout",
                }
            ]
        ).to_parquet(tratado_rotina, index=False)

        mock_tratar.return_value = str(tratado_rotina)
        dest_dir = Path(tmp) / "out"
        result = processar_e_salvar_monitor_eventos_tratado(
            monitor_csv,
            prod_csv,
            date(2026, 6, 18),
            pasta_destino=dest_dir,
        )
        assert result is not None
        assert result.name == "monitor-eventos-tratado_2026-06-18.parquet"
        captured = capsys.readouterr().out
        assert MONITOR_EVENTOS_SAVED_PREFIX in captured
        mock_tratar.assert_called_once()


@patch("app.infrastructure.brflow_monitor.processar_e_salvar_monitor_eventos_tratado")
@patch("app.infrastructure.brflow_monitor.navegar_monitor_e_baixar_csv")
def test_worker_brflow_calls_monitor_when_enabled(mock_nav, mock_process):
    from app.bots import bot_production as prod

    mock_nav.return_value = Path("fake.csv")
    mock_process.return_value = Path("fake-tratado.parquet")
    drv = MagicMock()
    with patch.object(prod, "create_driver", return_value=drv), patch.object(
        prod, "_fazer_login"
    ), patch.object(prod, "_navegar_e_configurar", return_value=MagicMock(date=lambda: date(2026, 6, 18))), patch.object(
        prod, "_extrair_csv_multiplos_dias", return_value=Path("prod.csv")
    ) as mock_extract, patch.object(
        prod, "_garantir_csv_com_dados", side_effect=lambda p, *_: p
    ), patch.object(
        prod, "safe_close_driver"
    ):
        result = prod._worker_brflow(
            "/tmp",
            "relatorio_produtividade",
            ".csv",
            "user",
            "pass",
            MagicMock(),
            {"baixar_monitor_com_producao": True, "dias_download_brflow": 7},
        )
    assert result["monitor_ok"] is True
    assert result["monitor_path"] == Path("fake-tratado.parquet")
    mock_nav.assert_called_once()
    assert mock_nav.call_args.kwargs.get("prefixo") == "relatorio_detalhado"
    mock_process.assert_called_once()
    assert mock_process.call_args.args[1] == Path("prod.csv")
    assert mock_process.call_args.kwargs["snapshot_at"].date() == mock_process.call_args.args[2]
    assert mock_process.call_args.kwargs["publicar"] is False
    assert mock_extract.call_args.kwargs["quantidade_dias"] == 7
    assert mock_extract.call_args.kwargs["arquivo_dia_atual"].name.endswith("_monitor_atual.csv")


def test_publica_monitor_base_apenas_quando_unificacao_nao_gerou_saida(tmp_path):
    from app.bots import bot_production as prod

    monitor_base = tmp_path / "monitor-eventos-tratado_2026-06-18.parquet"
    monitor_base.write_bytes(b"parquet-base")

    with patch(
        "app.infrastructure.brflow_monitor.publicar_monitor_eventos_tratado",
        return_value=monitor_base,
    ) as mock_publicar:
        result = prod._publicar_monitor_base_se_necessario(
            {"monitor_path": monitor_base},
            None,
        )
        final = prod._publicar_monitor_base_se_necessario(
            {"monitor_path": monitor_base},
            Path("monitor-unificado.parquet"),
        )

    assert result == monitor_base
    assert final == Path("monitor-unificado.parquet")
    mock_publicar.assert_called_once_with(monitor_base)


def test_extracao_multiplos_dias_processa_hoje_por_ultimo(tmp_path):
    from app.bots import bot_production as prod

    prod.parar_event.clear()
    periodos = []

    def registrar_periodo(data_dia, referencia):
        periodos.append((data_dia, referencia))
        return "inicio", "fim"

    with patch.object(prod, "_periodo_analise_hxh", side_effect=registrar_periodo), patch.object(
        prod, "_preencher_data_javascript"
    ), patch.object(
        prod, "_extrair_csv_dia_especifico", return_value=tmp_path / "dia.csv"
    ), patch.object(
        prod, "_ler_csv_robusto", return_value=(pd.DataFrame([{"id": 1}]), ";")
    ), patch.object(prod.time, "sleep"):
        result = prod._extrair_csv_multiplos_dias(
            MagicMock(),
            tmp_path,
            "relatorio",
            ".csv",
            quantidade_dias=5,
            arquivo_dia_atual=tmp_path / "monitor_atual.csv",
        )

    datas = [data_dia.date() for data_dia, _ in periodos]
    assert result is not None
    assert datas == sorted(datas)
    assert datas[-1] == prod.datetime.now(prod._get_tz_br()).date()
    assert (tmp_path / "monitor_atual.csv").is_file()


def test_gerar_sessoes_com_csv_prod_confer_do_ciclo():
    from app.bots.rotina.tasks.confer import _gerar_csv_sessoes_por_evento

    with TemporaryDirectory() as tmp:
        log_csv = Path(tmp) / "log_eventos.csv"
        prod_csv = Path(tmp) / "confer_prod.csv"
        pd.DataFrame(
            [
                {
                    "Matrícula do Colaborador": "C92928A",
                    "Nome do Colaborador": "Fulano",
                    "Data/Hora": "18/06/2026 08:00:00",
                    "Evento": "Autenticação com sucesso",
                },
                {
                    "Matrícula do Colaborador": "C92928A",
                    "Nome do Colaborador": "Fulano",
                    "Data/Hora": "18/06/2026 10:00:00",
                    "Evento": "Logout",
                },
            ]
        ).to_csv(log_csv, index=False, sep=";", encoding="utf-8-sig")
        pd.DataFrame(
            [
                {
                    "Matrícula do Colaborador": "C92928A",
                    "Nome do Colaborador": "Fulano",
                    "Data/Hora da Conferência": "18/06/2026 08:05:00",
                },
                {
                    "Matrícula do Colaborador": "C92928A",
                    "Nome do Colaborador": "Fulano",
                    "Data/Hora da Conferência": "18/06/2026 09:55:00",
                },
            ]
        ).to_csv(prod_csv, index=False, sep=";", encoding="utf-8-sig")

        sessoes = _gerar_csv_sessoes_por_evento(log_csv, matricula=None, csv_prod_confer=prod_csv)
        assert Path(sessoes).is_file()
        df = pd.read_csv(sessoes, sep=";", dtype=str)
        assert "matricula" in df.columns
        assert len(df) >= 1
        assert str(df.iloc[0]["matricula"]).lower() == "c92928a"


def test_gerar_sessoes_resolve_matricula_pelo_nome_do_relatorios():
    from app.bots.rotina.tasks.confer import _gerar_csv_sessoes_por_evento

    with TemporaryDirectory() as tmp:
        log_csv = Path(tmp) / "log_eventos_so_nome.csv"
        prod_csv = Path(tmp) / "confer_prod.csv"
        # Log Eventos HxH: só nome (sem matrícula válida)
        pd.DataFrame(
            [
                {
                    "Nome do Colaborador": "Maria Silva",
                    "Data/Hora": "18/06/2026 08:00:00",
                    "Evento": "Autenticação com sucesso",
                },
                {
                    "Nome do Colaborador": "Maria Silva",
                    "Data/Hora": "18/06/2026 10:00:00",
                    "Evento": "Logout",
                },
                {
                    "Nome do Colaborador": "Sem Match",
                    "Data/Hora": "18/06/2026 08:30:00",
                    "Evento": "Autenticação com sucesso",
                },
                {
                    "Nome do Colaborador": "Sem Match",
                    "Data/Hora": "18/06/2026 09:00:00",
                    "Evento": "Logout",
                },
            ]
        ).to_csv(log_csv, index=False, sep=";", encoding="utf-8-sig")
        pd.DataFrame(
            [
                {
                    "Matrícula do Colaborador": "C91123A",
                    "Nome do Colaborador": "Maria Silva",
                    "Data/Hora da Conferência": "18/06/2026 08:05:00",
                },
                {
                    "Matrícula do Colaborador": "C91123A",
                    "Nome do Colaborador": "Maria Silva",
                    "Data/Hora da Conferência": "18/06/2026 09:50:00",
                },
            ]
        ).to_csv(prod_csv, index=False, sep=";", encoding="utf-8-sig")

        sessoes = _gerar_csv_sessoes_por_evento(log_csv, matricula=None, csv_prod_confer=prod_csv)
        df = pd.read_csv(sessoes, sep=";", dtype=str)
        assert len(df) >= 2
        mats = [str(m).strip().lower() for m in df["matricula"].tolist()]
        assert "c91123a" in mats
        # Nome sem correspondência no Relatórios não inventa matrícula
        assert "" in mats or "sem match" in mats or any(
            m and not m.startswith("c") for m in mats
        )


def test_gerar_sessoes_resolve_quando_matricula_campo_tem_nome():
    from app.bots.rotina.tasks.confer import _gerar_csv_sessoes_por_evento

    with TemporaryDirectory() as tmp:
        log_csv = Path(tmp) / "log_eventos_nome_no_campo_mat.csv"
        prod_csv = Path(tmp) / "confer_prod.csv"
        pd.DataFrame(
            [
                {
                    "Matrícula do Colaborador": "JOAO SANTOS",
                    "Data/Hora": "18/06/2026 08:00:00",
                    "Evento": "Autenticação com sucesso",
                },
                {
                    "Matrícula do Colaborador": "JOAO SANTOS",
                    "Data/Hora": "18/06/2026 09:30:00",
                    "Evento": "Logout",
                },
            ]
        ).to_csv(log_csv, index=False, sep=";", encoding="utf-8-sig")
        pd.DataFrame(
            [
                {
                    "Matrícula do Colaborador": "C92222B",
                    "Nome do Colaborador": "Joao Santos",
                    "Data/Hora da Conferência": "18/06/2026 08:10:00",
                },
            ]
        ).to_csv(prod_csv, index=False, sep=";", encoding="utf-8-sig")

        sessoes = _gerar_csv_sessoes_por_evento(log_csv, matricula=None, csv_prod_confer=prod_csv)
        df = pd.read_csv(sessoes, sep=";", dtype=str)
        assert len(df) >= 1
        assert str(df.iloc[0]["matricula"]).lower() == "c92222b"


def test_unificar_monitor_hxh_com_confer_log_emits_hook(capsys):
    with TemporaryDirectory() as tmp:
        monitor_path = Path(tmp) / "monitor-eventos-tratado_2026-06-18.parquet"
        log_csv = Path(tmp) / "log_eventos.csv"
        prod_csv = Path(tmp) / "confer_prod.csv"
        dest = Path(tmp) / "out"

        pd.DataFrame(
            [
                {
                    "Data": date(2026, 6, 18),
                    "Hora": 9,
                    "Usuário": "c92928a",
                    "Data do Evento": pd.Timestamp("2026-06-18 09:00:00"),
                    "Evento": "Autenticação com sucesso",
                    "Data segundo evento": pd.Timestamp("2026-06-18 11:00:00"),
                    "Segundo evento": "Logout",
                }
            ]
        ).to_parquet(monitor_path, index=False)

        pd.DataFrame(
            [
                {
                    "Matrícula do Colaborador": "C97960A",
                    "Nome do Colaborador": "Beltrano",
                    "Data/Hora": "18/06/2026 08:00:00",
                    "Evento": "Autenticação com sucesso",
                },
                {
                    "Matrícula do Colaborador": "C97960A",
                    "Nome do Colaborador": "Beltrano",
                    "Data/Hora": "18/06/2026 09:30:00",
                    "Evento": "Logout",
                },
            ]
        ).to_csv(log_csv, index=False, sep=";", encoding="utf-8-sig")
        pd.DataFrame(
            [
                {
                    "Matrícula do Colaborador": "C97960A",
                    "Data/Hora da Conferência": "18/06/2026 08:10:00",
                },
                {
                    "Matrícula do Colaborador": "C97960A",
                    "Data/Hora da Conferência": "18/06/2026 09:20:00",
                },
            ]
        ).to_csv(prod_csv, index=False, sep=";", encoding="utf-8-sig")

        result = unificar_monitor_hxh_com_confer_log(
            monitor_path,
            log_csv,
            date(2026, 6, 18),
            csv_prod_confer=prod_csv,
            pasta_destino=dest,
        )
        assert result is not None
        assert result.name == "monitor-eventos-tratado_2026-06-18.parquet"
        df = pd.read_parquet(result)
        assert len(df) >= 2
        usuarios = {str(u).lower() for u in df["Usuário"].tolist()}
        assert "c92928a" in usuarios
        assert "c97960a" in usuarios
        captured = capsys.readouterr().out
        assert MONITOR_EVENTOS_SAVED_PREFIX in captured


@patch("app.bots.bot_production.baixar_log_eventos_confer_dia")
@patch("app.bots.bot_production.baixar_relatorio_producao_confer")
def test_worker_confer_downloads_log_eventos_when_enabled(mock_prod, mock_log):
    from app.bots import bot_production as prod

    mock_prod.return_value = Path("/tmp/confer.csv")
    mock_log.return_value = Path("/tmp/log.csv")
    drv = MagicMock()

    with patch.object(prod, "create_driver", return_value=drv), patch.object(
        prod, "_fazer_login"
    ), patch.object(prod, "_navegar_busca_confer"), patch.object(
        prod, "_garantir_csv_com_dados", side_effect=lambda p, *_: p
    ), patch.object(prod, "safe_close_driver"), patch.object(
        Path, "mkdir", return_value=None
    ), patch.object(Path, "is_file", return_value=True):
        result = prod._worker_confer(
            "/tmp/production",
            "user",
            "pass",
            {"baixar_log_eventos_com_producao": True},
        )

    assert result["type"] == "confer"
    assert result["log_eventos_ok"] is True
    mock_log.assert_called_once()
