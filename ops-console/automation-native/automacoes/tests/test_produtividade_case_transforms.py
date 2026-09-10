"""Testes de transforms e pipeline match (sem Mongo)."""

from datetime import datetime, timezone

from app.bots.produtividade_case.transforms import (
    build_consolidado_row,
    join_alerts,
    join_all_alerts,
    match_consolidado_filter,
    rows_from_aggregate,
)


def test_join_all_alerts():
    assert join_all_alerts(None) == ""
    assert join_all_alerts([{"name": "A"}, {"name": "B"}]) == "A | B"
    assert join_all_alerts([{"x": 1}, "x"]) == ""


def test_join_alerts_only_checked():
    alerts = [
        {"name": "ok", "checked": True},
        {"name": "skip", "checked": False},
        {"name": "also", "checked": True},
    ]
    assert join_alerts(alerts, only_checked=True) == "ok | also"
    assert "skip" in join_alerts(alerts, only_checked=False)


def test_match_consolidado_filter():
    ini = datetime(2026, 5, 1, 6, 0, tzinfo=timezone.utc)
    fim = datetime(2026, 5, 16, 6, 0, tzinfo=timezone.utc)
    filt = match_consolidado_filter(ini, fim)
    assert filt["transactionStatus"] == "COMPLETED"
    assert filt["origin.requestType"] == "AUDIT"
    assert filt["conclusionDate"]["$gte"] == ini
    assert filt["conclusionDate"]["$lt"] == fim


def test_build_consolidado_row_and_rows_from_aggregate():
    blocked = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    conc = datetime(2026, 5, 10, 12, 5, tzinfo=timezone.utc)
    doc = {
        "_id": "abc123",
        "cliente_origem": "Cliente X",
        "workflow_origem": "WF",
        "nh_origem": "1",
        "usuario_origem": "u1",
        "protocolo_origem": "P1",
        "cpf": "000",
        "contrato": "C1",
        "cad_origem": blocked,
        "blocked_date": blocked,
        "conc_origem": conc,
        "resultado_origem": "OK",
        "status_origem": "COMPLETED",
        "alerts_origem": [{"name": "Alerta1"}],
        "matricula_origem": "1234567",
        "tipo_conc_origem": "Manual",
        "cad_dest": blocked,
        "conc_dest": conc,
        "resultado_dest": "APPROVED",
        "status_dest": "COMPLETED",
        "matricula_dest": "c92928a",
        "alerts_dest": [{"name": "Chk", "checked": True}],
    }
    row = build_consolidado_row(doc)
    assert row["Protocolo Destino"] == "abc123"
    assert row["Cliente Destino"] == "GA - Case Manager"
    assert row["Matricula Destino"] == "c92928a"
    assert row["Tempo de Analise"] == "00:05:00"
    assert row["Alertas Origem"] == "Alerta1"
    assert row["Alertas Destino"] == "Chk"
    # 12:00 UTC → 09:00 Brasília
    assert row["Data Conclusao Destino"] == "10/05/2026"
    assert row["Hora Conclusao Destino"] == "09:05:00"
    assert row["Hora Inspecao"] == "09:00:00"

    rows = rows_from_aggregate([doc])
    assert len(rows) == 1


def test_split_datetime_br_naive_utc_from_mongo():
    """PyMongo devolve naive UTC; não pode herdar o fuso do SO (ex.: SP)."""
    from app.bots.produtividade_case.transforms import split_datetime_br

    naive_utc = datetime(2026, 5, 10, 12, 0, 0)  # sem tzinfo = UTC do DocumentDB
    data, hora = split_datetime_br(naive_utc)
    assert data == "10/05/2026"
    assert hora == "09:00:00"
