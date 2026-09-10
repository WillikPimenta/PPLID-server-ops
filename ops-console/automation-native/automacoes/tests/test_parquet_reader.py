"""Tests for OneDrive-safe parquet reading."""

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.infrastructure import parquet_reader as pr
from app.bots.replicacao_aud_planning import PREFIXO_DETALHADO_FINAL, resolver_parquet_d1


def _write_parquet(path: Path, rows: int = 3) -> None:
    pd.DataFrame({"a": list(range(rows))}).to_parquet(path)


def test_parquet_legivel_valid():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.parquet"
        _write_parquet(path)
        assert pr.parquet_legivel(path) is True


def test_parquet_legivel_missing():
    assert pr.parquet_legivel(Path("/nonexistent/file.parquet")) is False


def test_ler_parquet_local():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "local.parquet"
        _write_parquet(path, 5)
        df = pr.ler_parquet(path)
        assert len(df) == 5


def test_ler_parquet_onedrive_cache_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        onedrive = Path(tmp) / "OneDrive - CORP" / "data"
        onedrive.mkdir(parents=True)
        path = onedrive / "file.parquet"
        _write_parquet(path)

        df_expected = pd.DataFrame({"x": [1, 2]})
        call_count = {"n": 0}

        def fake_read(parquet_path, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError(22, "Invalid argument")
            return df_expected

        cache_dir = Path(tmp) / "cache"
        with patch.object(pr.pd, "read_parquet", side_effect=fake_read):
            with patch.object(pr, "_CACHE_PARQUET", cache_dir):
                with patch("app.infrastructure.parquet_reader.shutil.copy2") as mock_copy:
                    mock_copy.side_effect = lambda src, dst: Path(dst).write_bytes(
                        Path(src).read_bytes()
                    )
                    result = pr.ler_parquet(path)

        assert len(result) == 2
        mock_copy.assert_called_once()


def test_ler_parquet_onedrive_failure_message():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "OneDrive - CORP" / "broken.parquet"
        path.parent.mkdir(parents=True)
        path.write_text("not parquet", encoding="utf-8")

        with patch.object(pr.pd, "read_parquet", side_effect=OSError(22, "Invalid argument")):
            with patch("app.infrastructure.parquet_reader.shutil.copy2", side_effect=OSError(22, "Invalid argument")):
                with pytest.raises(OSError, match="Parquet inacessível no OneDrive"):
                    pr.ler_parquet(path)


def test_resolver_parquet_d1_returns_legivel_d1():
    with tempfile.TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        data_ref = datetime(2026, 6, 8)
        nome = f"{PREFIXO_DETALHADO_FINAL}20260608.parquet"
        _write_parquet(pasta / nome)
        result = resolver_parquet_d1(data_ref=data_ref, pasta=pasta)
        assert result.name == nome


def test_resolver_parquet_d1_fallback_when_d1_ilegivel():
    with tempfile.TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        data_ref = datetime(2026, 6, 8)
        nome_d1 = f"{PREFIXO_DETALHADO_FINAL}20260608.parquet"
        nome_ant = f"{PREFIXO_DETALHADO_FINAL}20260607.parquet"
        (pasta / nome_d1).write_text("placeholder", encoding="utf-8")
        _write_parquet(pasta / nome_ant)

        result = resolver_parquet_d1(data_ref=data_ref, pasta=pasta, fallback_ultimo=True)
        assert result.name == nome_ant


def test_resolver_parquet_d1_raises_when_d1_ilegivel_no_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        data_ref = datetime(2026, 6, 8)
        nome_d1 = f"{PREFIXO_DETALHADO_FINAL}20260608.parquet"
        (pasta / nome_d1).write_text("placeholder", encoding="utf-8")

        with pytest.raises(FileNotFoundError, match="inacessível no OneDrive"):
            resolver_parquet_d1(data_ref=data_ref, pasta=pasta, fallback_ultimo=False)
