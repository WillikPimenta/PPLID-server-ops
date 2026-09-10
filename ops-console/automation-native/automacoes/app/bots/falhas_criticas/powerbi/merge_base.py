"""Consolida export do Power BI na aba Base de FALHAS_CRITICAS_MANUAL.xlsx."""
from __future__ import annotations

import posixpath
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

import app.bots.falhas_criticas.powerbi.config as config
from app.bots.falhas_criticas.reveal_process import is_reveal_requested

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ET.register_namespace("", MAIN_NS)


def _log(msg: str) -> None:
    print(f"[merge] {msg}")


@dataclass
class MergeStats:
    rows_before: int = 0
    rows_after: int = 0
    rows_added: int = 0
    rows_imported: int = 0
    duplicates_removed: int = 0
    warnings: list[str] = field(default_factory=list)

    def status_message(self) -> str:
        if self.rows_added > 0:
            return f"Merge: +{self.rows_added} linha(s) na Base ({self.rows_before}→{self.rows_after})"
        if self.duplicates_removed > 0:
            return (
                f"Merge: {self.duplicates_removed} linha(s) atualizada(s) "
                f"({self.rows_before}→{self.rows_after})"
            )
        return f"Merge: sem alterações ({self.rows_before} linhas)"


def _parse_br_date(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            return None
        return parsed.date()


def _dedupe_keys(df: pd.DataFrame) -> list[str]:
    keys: list[str] = []
    if "Protocolo" in df.columns:
        keys.append("Protocolo")
    if "Data de Análise" in df.columns:
        keys.append("Data de Análise")
    return keys


def should_persist_merge(rows_added: int, duplicates_removed: int) -> bool:
    """Salva a Base quando há linhas novas ou export sobrescreve registros existentes."""
    return rows_added > 0 or duplicates_removed > 0


def _dedupe_combined(df_base: pd.DataFrame, df_new: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Concatena base+export e deduplica por Protocolo+Data (export prevalece)."""
    combined = pd.concat([df_base, df_new], ignore_index=True)
    rows_before_dedup = len(combined)
    keys = _dedupe_keys(combined)
    if keys:
        combined = combined.drop_duplicates(subset=keys, keep="last")
    else:
        combined = combined.drop_duplicates(keep="first")
    duplicates_removed = rows_before_dedup - len(combined)
    return combined, duplicates_removed


def validate_export_row_count(
    df_new: pd.DataFrame,
    period_start: date | None,
    period_end: date | None,
) -> list[str]:
    """Alerta se o export parece maior que o período filtrado no Power BI."""
    warnings: list[str] = []
    rows_imported = len(df_new)
    if period_start is None or period_end is None:
        return warnings

    days = (period_end - period_start).days + 1
    if days <= 1 and rows_imported > 50:
        warnings.append(
            f"AVISO: export com {rows_imported} linhas para período de 1 dia "
            f"({period_start:%d/%m/%Y}) — verifique filtros no Power BI"
        )
    elif days <= 7 and rows_imported > 500:
        warnings.append(
            f"AVISO: export com {rows_imported} linhas para {days} dia(s) — verifique filtros no Power BI"
        )

    if "Data de Análise" in df_new.columns and not df_new.empty:
        col = pd.to_datetime(df_new["Data de Análise"], errors="coerce")
        valid = col.dropna()
        if not valid.empty:
            vmin = valid.min().date()
            vmax = valid.max().date()
            if vmin < period_start or vmax > period_end:
                warnings.append(
                    f"AVISO: datas no export ({vmin:%d/%m/%Y}–{vmax:%d/%m/%Y}) "
                    f"fora do período ({period_start:%d/%m/%Y}–{period_end:%d/%m/%Y})"
                )
    return warnings


def _normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for col in df.columns:
        col_str = str(col).strip()
        norm = col_str.lower()
        if norm in ("responsável", "responsavel"):
            rename[col] = "Líder"
        elif norm in ("líder", "lider") and col_str != "Líder":
            rename[col] = "Líder"
    if rename:
        df = df.rename(columns=rename)
    return df


def _normalize_protocolo(val):
    if pd.isna(val) or val is None:
        return pd.NA
    if isinstance(val, str) and not val.strip():
        return pd.NA
    return pd.to_numeric(val, errors="coerce")


def _as_integer_series(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().any() and (numeric.dropna() % 1 == 0).all():
        return numeric.astype("Int64")
    return numeric


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "Protocolo" in df.columns:
        df["Protocolo"] = _as_integer_series(df["Protocolo"].apply(_normalize_protocolo))

    for col in ("Data Auditoria", "Data de Análise"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce")
            df[col] = parsed.dt.normalize()
    return df


def _format_sheet_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "Protocolo" in df.columns:
        df["Protocolo"] = _as_integer_series(df["Protocolo"])

    for col in ("Data Auditoria", "Data de Análise"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce")
            df[col] = parsed.dt.date
    return df


def _read_base_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    last_exc: BaseException | None = None
    for attempt in range(1, 6):
        try:
            return pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
        except PermissionError as exc:
            raise PermissionError(
                f"Não foi possível ler {path} — feche a planilha no Excel e tente novamente."
            ) from exc
        except OSError as exc:
            last_exc = exc
            errno = getattr(exc, "errno", None)
            if errno == 13 or "Permission denied" in str(exc):
                raise PermissionError(
                    f"Não foi possível ler {path} — feche a planilha no Excel e tente novamente."
                ) from exc
            # OneDrive / Excel: Errno 22 Invalid argument enquanto o arquivo sincroniza
            if errno in (22, 2) and attempt < 5:
                time.sleep(0.6 * attempt)
                continue
            raise
    assert last_exc is not None
    raise last_exc


def _latest_master_backups(path: Path, limit: int = 5) -> list[Path]:
    backups_dir = path.parent / "backups"
    if not backups_dir.is_dir():
        return []
    return sorted(
        backups_dir.glob("FALHAS_CRITICAS_MANUAL_*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]


def ensure_master_workbook(
    path: Path | str,
    *,
    backup_path: Path | str | None = None,
    wait_s: float = 10.0,
) -> Path:
    """Garante que a master exista após RefreshAll/OneDrive; restaura backup se sumir."""
    path = Path(path)
    deadline = time.time() + max(0.0, wait_s)
    while True:
        if path.is_file():
            return path
        if time.time() >= deadline:
            break
        time.sleep(0.5)

    candidates: list[Path] = []
    if backup_path:
        hint = Path(backup_path)
        if hint.is_file():
            candidates.append(hint)
    candidates.extend(_latest_master_backups(path))

    seen: set[str] = set()
    for src in candidates:
        key = str(src.resolve()) if src.exists() else str(src)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not src.is_file():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, path)
            if path.is_file():
                _log(f"Master ausente após Excel/OneDrive — restaurada de {src.name}")
                return path
        except OSError as exc:
            _log(f"Falha ao restaurar master de {src.name}: {exc}")

    raise FileNotFoundError(
        f"Planilha master sumiu após refresh do Excel: {path}. "
        f"Restaure o backup mais recente em {(path.parent / 'backups')}."
    )


def _excel_value(val) -> object | None:
    if val is None or val is pd.NA:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime().replace(tzinfo=None)
    if isinstance(val, date) and not isinstance(val, datetime):
        return datetime(val.year, val.month, val.day)
    if hasattr(val, "item"):
        try:
            return _excel_value(val.item())
        except (ValueError, AttributeError):
            pass
    return val


def _build_temp_workbook_bytes(df: pd.DataFrame) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Base"

    headers = list(df.columns)
    ws.append(headers)

    date_cols = {name for name in ("Data Auditoria", "Data de Análise") if name in headers}
    proto_idx = headers.index("Protocolo") + 1 if "Protocolo" in headers else None

    for row in df.itertuples(index=False):
        values = [_excel_value(v) for v in row]
        ws.append(values)
        row_idx = ws.max_row
        for col_name in date_cols:
            col_idx = headers.index(col_name) + 1
            ws.cell(row=row_idx, column=col_idx).number_format = "DD/MM/YYYY"
        if proto_idx:
            ws.cell(row=row_idx, column=proto_idx).number_format = "0"

    for col_idx, col_name in enumerate(headers, start=1):
        if col_name in date_cols:
            ws.cell(row=1, column=col_idx).number_format = "DD/MM/YYYY"
        elif col_name == "Protocolo":
            ws.cell(row=1, column=col_idx).number_format = "0"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet_path_for_name(zf: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"].lstrip("/")
        for rel in rels
        if "worksheets/" in rel.attrib.get("Target", "")
    }

    for sheet in workbook.iter():
        if not sheet.tag.endswith("sheet"):
            continue
        if sheet.attrib.get("name") != sheet_name:
            continue
        rel_id = sheet.attrib.get(f"{{{REL_NS}}}id")
        target = rel_map.get(rel_id)
        if not target:
            raise KeyError(f"Relacionamento da aba '{sheet_name}' não encontrado.")
        return f"xl/{target}"
    raise KeyError(f"Aba '{sheet_name}' não encontrada no workbook.")


def _copy_column_styles(old_root: ET.Element, new_sheet_data: ET.Element) -> None:
    old_data = old_root.find(f"{{{MAIN_NS}}}sheetData")
    if old_data is None:
        return

    def _cells_by_column(row: ET.Element) -> dict[str, ET.Element]:
        mapping: dict[str, ET.Element] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            ref = cell.attrib.get("r", "")
            match = re.match(r"([A-Z]+)", ref)
            if match:
                mapping[match.group(1)] = cell
        return mapping

    old_rows = old_data.findall(f"{{{MAIN_NS}}}row")
    if len(old_rows) < 2:
        return

    style_by_col = {
        col: cell.attrib["s"]
        for col, cell in _cells_by_column(old_rows[1]).items()
        if "s" in cell.attrib
    }

    for row in new_sheet_data.findall(f"{{{MAIN_NS}}}row"):
        row_idx = int(row.attrib.get("r", "0"))
        if row_idx < 2:
            continue
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            ref = cell.attrib.get("r", "")
            match = re.match(r"([A-Z]+)", ref)
            if not match:
                continue
            col = match.group(1)
            if col in style_by_col:
                cell.attrib["s"] = style_by_col[col]
            elif "s" in cell.attrib:
                del cell.attrib["s"]


def _replace_sheet_data_in_xml(old_text: str, new_data_xml: str) -> tuple[str, bool]:
    """Substitui o bloco sheetData no XML da planilha.

    Retorna (texto_atualizado, alterou_conteúdo).
    """
    match = re.search(
        r"<sheetData\b[^>]*>.*?</sheetData>|<sheetData\b[^>]*/>",
        old_text,
        flags=re.DOTALL,
    )
    if not match:
        return old_text, False
    if match.group() == new_data_xml:
        return old_text, False
    return old_text[: match.start()] + new_data_xml + old_text[match.end() :], True


def _patch_sheet_xml(
    old_sheet_xml: bytes,
    new_sheet_data: ET.Element,
    num_rows: int,
    num_cols: int,
) -> tuple[bytes, bool]:
    """Substitui apenas sheetData/dimension, preservando namespaces do XML original."""
    old_root = ET.fromstring(old_sheet_xml)
    if old_root.find(f"{{{MAIN_NS}}}sheetData") is None:
        raise RuntimeError("Não foi possível localizar <sheetData> na aba Base.")

    _copy_column_styles(old_root, new_sheet_data)

    new_data_xml = ET.tostring(new_sheet_data, encoding="unicode")
    new_data_xml = re.sub(r'\sxmlns="[^"]+"', "", new_data_xml, count=1)
    old_text = old_sheet_xml.decode("utf-8")
    patched, data_changed = _replace_sheet_data_in_xml(old_text, new_data_xml)
    if not re.search(
        r"<sheetData\b[^>]*>.*?</sheetData>|<sheetData\b[^>]*/>",
        old_text,
        flags=re.DOTALL,
    ):
        raise RuntimeError("Não foi possível localizar <sheetData> na aba Base.")
    if not data_changed:
        _log("Conteúdo da aba Base já estava sincronizado no arquivo.")

    dimension_ref = f"A1:{get_column_letter(num_cols)}{num_rows}"
    patched, count = re.subn(
        r'(<dimension\b[^>]*\bref=")[^"]*(")',
        rf"\g<1>{dimension_ref}\g<2>",
        patched,
        count=1,
    )
    if count == 0:
        patched = patched.replace(
            "<sheetViews>",
            f'<dimension ref="{dimension_ref}"/><sheetViews>',
            1,
        )
    changed = data_changed or patched != old_text
    return patched.encode("utf-8"), changed


def _strip_calc_chain(entries: dict[str, bytes]) -> None:
    entries.pop("xl/calcChain.xml", None)

    rels_key = "xl/_rels/workbook.xml.rels"
    if rels_key in entries:
        rels_text = entries[rels_key].decode("utf-8")
        rels_text = re.sub(
            r'<Relationship\b[^>]*\bTarget="calcChain\.xml"[^>]*/>\s*',
            "",
            rels_text,
        )
        entries[rels_key] = rels_text.encode("utf-8")

    ct_key = "[Content_Types].xml"
    if ct_key in entries:
        ct_text = entries[ct_key].decode("utf-8")
        ct_text = re.sub(
            r'<Override\b[^>]*PartName="/xl/calcChain\.xml"[^>]*/>\s*',
            "",
            ct_text,
        )
        entries[ct_key] = ct_text.encode("utf-8")


def _update_table_xml(xml_bytes: bytes, num_rows: int, num_cols: int) -> bytes:
    ref = f"A1:{get_column_letter(num_cols)}{num_rows}"
    text = xml_bytes.decode("utf-8")
    text, count = re.subn(
        r'(<table\b[^>]*\bref=")[^"]*(")',
        rf"\g<1>{ref}\g<2>",
        text,
        count=1,
    )
    if count == 0:
        raise RuntimeError("Não foi possível atualizar o intervalo da tabela Excel.")

    text, _ = re.subn(
        r'(<autoFilter\b[^>]*\bref=")[^"]*(")',
        rf"\g<1>{ref}\g<2>",
        text,
        count=1,
    )
    return text.encode("utf-8")


def _table_targets_for_sheet(zf: zipfile.ZipFile, sheet_path: str) -> list[str]:
    rel_path = sheet_path.replace("worksheets/", "worksheets/_rels/") + ".rels"
    if rel_path not in zf.namelist():
        return []

    sheet_dir = posixpath.dirname(sheet_path)
    rels = ET.fromstring(zf.read(rel_path))
    targets: list[str] = []
    for rel in rels:
        target = rel.attrib.get("Target", "")
        if "tables/table" not in target:
            continue
        resolved = posixpath.normpath(posixpath.join(sheet_dir, target)).replace("\\", "/")
        if resolved in zf.namelist():
            targets.append(resolved)
    return targets


def _update_base_sheet_via_zip(path: Path, sheet_name: str, df: pd.DataFrame) -> None:
    """Substitui somente sheetData da aba Base dentro do .xlsx, preservando queries."""
    df = _format_sheet_for_excel(df)
    num_rows = len(df) + 1
    num_cols = len(df.columns)

    temp_bytes = _build_temp_workbook_bytes(df)
    with zipfile.ZipFile(BytesIO(temp_bytes)) as temp_zip:
        new_sheet_xml = temp_zip.read("xl/worksheets/sheet1.xml")

    new_root = ET.fromstring(new_sheet_xml)
    new_sheet_data = new_root.find(f"{{{MAIN_NS}}}sheetData")
    if new_sheet_data is None:
        raise RuntimeError("sheetData não encontrado no XML temporário da aba Base.")

    with zipfile.ZipFile(path, "r") as zf:
        sheet_path = _sheet_path_for_name(zf, sheet_name)
        old_sheet_xml = zf.read(sheet_path)
        table_targets = _table_targets_for_sheet(zf, sheet_path)

        patched_sheet_xml, sheet_changed = _patch_sheet_xml(
            old_sheet_xml, new_sheet_data, num_rows, num_cols
        )

        entries: dict[str, bytes] = {}
        any_change = sheet_changed
        for name in zf.namelist():
            data = zf.read(name)
            if name == sheet_path:
                entries[name] = patched_sheet_xml
            elif name in table_targets:
                updated_table = _update_table_xml(data, num_rows, num_cols)
                if updated_table != data:
                    any_change = True
                    entries[name] = updated_table
                    _log(
                        f"Tabela Excel expandida: {name} -> "
                        f"A1:{get_column_letter(num_cols)}{num_rows}"
                    )
                else:
                    entries[name] = data
            else:
                entries[name] = data

    if not any_change:
        _log("Arquivo master já estava atualizado — gravação ignorada.")
        return

    _strip_calc_chain(entries)

    tmp_path = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)
    tmp_path.replace(path)


def _com_hresults(exc: BaseException) -> list[int]:
    """Coleta HRESULTs do erro COM (externo e aninhado em excepinfo)."""
    found: list[int] = []
    args = getattr(exc, "args", ())
    if args:
        try:
            if len(args) >= 3 and isinstance(args[2], tuple) and len(args[2]) > 5:
                nested = args[2][5]
                if nested is not None:
                    found.append(int(nested))
        except (TypeError, ValueError):
            pass
        try:
            found.append(int(args[0]))
        except (TypeError, ValueError):
            pass
    hresult = getattr(exc, "hresult", None)
    if hresult is not None:
        try:
            found.append(int(hresult))
        except (TypeError, ValueError):
            pass
    # Preserva ordem mas remove duplicatas
    out: list[int] = []
    for code in found:
        if code not in out:
            out.append(code)
    return out


def _com_hresult(exc: BaseException) -> int | None:
    codes = _com_hresults(exc)
    return codes[0] if codes else None


def _is_transient_excel_busy(exc: BaseException) -> bool:
    """Excel temporariamente indisponível (queries atualizando, diálogo modal, etc.)."""
    transient = {
        -2147418111,  # RPC_E_CALL_REJECTED
        -2146777998,  # VBA_E_IGNORE / OLE 0x800AC372 — comum durante RefreshAll
    }
    for hresult in _com_hresults(exc):
        if hresult in transient:
            return True
        if (hresult & 0xFFFF) in {0xC472, 0xC372}:
            return True
    return False


# HRESULT comum quando Save() falha após RefreshAll (OneDrive / sessão Excel).
_EXCEL_SAVE_FAILED_HRESULTS = {
    -2146827284,  # 0x800A03EC — "Documento não salvo"
}


def _is_excel_save_error(exc: BaseException) -> bool:
    for hresult in _com_hresults(exc):
        if hresult in _EXCEL_SAVE_FAILED_HRESULTS:
            return True
        if (hresult & 0xFFFFFFFF) == 0x800A03EC:
            return True
    return False


def _save_workbook_robust(wb, path: Path) -> None:
    """Salva workbook após RefreshAll; fallback SaveAs / cópia local (OneDrive)."""
    import pythoncom

    abs_path = str(path.resolve())
    xl_openxml_workbook = 51
    xl_local_session_changes = 2

    pythoncom.PumpWaitingMessages()
    time.sleep(0.5)

    def _save():
        wb.Save()

    def _save_as(filename: str):
        wb.SaveAs(
            Filename=filename,
            FileFormat=xl_openxml_workbook,
            ConflictResolution=xl_local_session_changes,
        )

    try:
        _com_retry(_save, attempts=15, delay=0.5)
        return
    except Exception as exc:
        if not _is_excel_save_error(exc):
            raise
        _log(
            "Save() rejeitado pelo Excel após refresh "
            f"(HRESULT {_com_hresult(exc)}); tentando SaveAs..."
        )

    try:
        _com_retry(lambda: _save_as(abs_path), attempts=10, delay=1.0)
        return
    except Exception as exc:
        if not _is_excel_save_error(exc):
            raise
        _log("SaveAs no caminho original falhou — salvando via cópia local temporária...")

    tmp_path = path.with_name(f".{path.stem}_refresh_{int(time.time())}.xlsx")
    try:
        _com_retry(lambda: _save_as(str(tmp_path)), attempts=10, delay=1.0)
        shutil.copy2(tmp_path, path)
        _log(f"Planilha copiada de {tmp_path.name} para {path.name}.")
    finally:
        tmp_path.unlink(missing_ok=True)


def _com_retry(action, *, attempts: int = 40, delay: float = 0.25):
    try:
        import pythoncom
        import pywintypes
    except ImportError:
        return action()

    last_error = None
    for _ in range(attempts):
        pythoncom.PumpWaitingMessages()
        try:
            return action()
        except pywintypes.com_error as exc:
            last_error = exc
            if _is_transient_excel_busy(exc):
                time.sleep(delay)
                continue
            raise
    raise last_error


def _safe_excel_shutdown(wb, excel) -> None:
    if wb is not None:
        try:
            _com_retry(lambda: wb.Close(SaveChanges=False), attempts=5, delay=0.5)
        except Exception:
            pass
    if excel is not None:
        try:
            _com_retry(lambda: excel.Quit(), attempts=5, delay=0.5)
        except Exception:
            pass


def _try_refresh_all(wb, *, attempts: int = 180, delay: float = 2.0) -> bool:
    """Tenta iniciar RefreshAll. Retorna True se a chamada foi aceita pelo Excel."""
    try:
        import pythoncom
        import pywintypes
    except ImportError:
        wb.RefreshAll()
        return True

    last_busy_log = 0.0
    for attempt in range(attempts):
        pythoncom.PumpWaitingMessages()
        try:
            wb.RefreshAll()
            return True
        except pywintypes.com_error as exc:
            if _is_transient_excel_busy(exc):
                now = time.time()
                if now - last_busy_log >= 15.0:
                    _log(
                        "Excel atualizando consultas — aguardando liberar "
                        f"({attempt + 1}/{attempts})..."
                    )
                    last_busy_log = now
                time.sleep(delay)
                continue
            _log(f"RefreshAll interrompido (erro não transitório): {exc}")
            return False
    _log("RefreshAll não iniciou a tempo — Excel ainda ocupado com atualização anterior.")
    return False


def _prompt_refresh_wait() -> None:
    config.wait_for_user(
        "Excel atualizando Power Query — aguarde o carregamento terminar "
        "(barra 'Consultando…'). Só interaja se aparecer login Microsoft."
    )


def _prompt_manual_refresh() -> None:
    config.wait_for_user(
        "Merge concluído. No Excel: aguarde as queries terminarem e salve (Ctrl+S) se necessário."
    )


def _run_refresh_all_with_login(wb, excel) -> bool:
    """Executa RefreshAll; aguarda Excel liberar se estiver atualizando queries."""
    import pythoncom

    _log("Executando RefreshAll...")
    if _try_refresh_all(wb):
        _wait_for_queries_done(excel, wb)
        return True

    _log("Excel ainda ocupado — aguardando automaticamente antes de nova tentativa...")
    for attempt in range(5):
        if attempt > 0:
            _log(f"Nova tentativa de RefreshAll ({attempt + 1}/5)...")
        _prompt_refresh_wait()
        pythoncom.PumpWaitingMessages()
        time.sleep(3)
        if _try_refresh_all(wb, attempts=180, delay=2.0):
            _wait_for_queries_done(excel, wb)
            return True

    return False


def _wait_for_queries_done(excel, wb=None) -> None:
    import pythoncom

    try:
        import pywintypes
    except ImportError:
        pywintypes = None

    deadline = time.time() + config.QUERY_REFRESH_TIMEOUT_SEC
    login_prompted = False
    while time.time() < deadline:
        pythoncom.PumpWaitingMessages()
        try:
            excel.CalculateUntilAsyncQueriesDone()
            _log("Todas as queries assíncronas concluídas.")
            return
        except AttributeError:
            break
        except Exception as exc:
            if (
                pywintypes is not None
                and isinstance(exc, pywintypes.com_error)
                and _is_transient_excel_busy(exc)
                and wb is not None
                and not login_prompted
            ):
                _log("Queries ainda processando — aguardando Excel concluir...")
                _prompt_refresh_wait()
                login_prompted = True
                pythoncom.PumpWaitingMessages()
                _try_refresh_all(wb)
                continue

        try:
            if int(excel.CalculationState) == 0:
                time.sleep(2)
                return
        except Exception:
            pass
        time.sleep(2)

    _log(
        f"Aviso: timeout de {config.QUERY_REFRESH_TIMEOUT_SEC}s aguardando queries — "
        "salvando planilha mesmo assim."
    )


def _apply_excel_visibility(excel) -> None:
    """Respeita modo 2º plano; revela se o usuário pediu via portal."""
    visible = bool(config.EXCEL_VISIBLE) or is_reveal_requested()
    try:
        excel.Visible = visible
    except Exception:
        pass


def refresh_workbook_queries(path: Path, *, backup_path: Path | None = None) -> bool:
    """Abre o Excel, executa RefreshAll e salva a planilha. Retorna True se concluiu."""
    try:
        import pythoncom
        import win32com.client as win32
    except ImportError as exc:
        raise RuntimeError(
            "pywin32 é necessário para atualizar as queries. Instale com: pip install pywin32"
        ) from exc

    path = Path(path)
    abs_path = str(path.resolve())
    pythoncom.CoInitialize()
    excel = None
    wb = None
    refresh_ok = False
    try:
        _log("Abrindo Excel para atualizar todas as queries (pode demorar)...")
        excel = _com_retry(lambda: win32.Dispatch("Excel.Application"))
        _apply_excel_visibility(excel)
        excel.DisplayAlerts = False
        excel.ScreenUpdating = True
        excel.EnableEvents = False
        excel.AskToUpdateLinks = False

        wb = _com_retry(
            lambda: excel.Workbooks.Open(
                abs_path,
                UpdateLinks=0,
                ReadOnly=False,
                IgnoreReadOnlyRecommended=True,
                Notify=False,
                AddToMru=False,
            ),
            attempts=80,
            delay=0.5,
        )
        time.sleep(1)
        pythoncom.PumpWaitingMessages()

        try:
            query_count = int(wb.Queries.Count)
            _log(f"Power Query: {query_count} consulta(s) detectada(s).")
        except Exception:
            pass

        try:
            conn_count = int(wb.Connections.Count)
            _log(f"Conexões: {conn_count} detectada(s).")
        except Exception:
            pass

        if _run_refresh_all_with_login(wb, excel):
            _save_workbook_robust(wb, path)
            _log("Planilha salva após atualização das queries.")
            refresh_ok = True
        else:
            _log(
                "RefreshAll automático não concluiu — o Excel permanecerá aberto para conclusão manual."
            )
            _prompt_manual_refresh()
            try:
                _save_workbook_robust(wb, path)
                _log("Planilha salva.")
                refresh_ok = True
            except Exception:
                _log("AVISO: Salve a planilha manualmente no Excel (Ctrl+S).")
    except Exception as exc:
        _log(
            f"AVISO: Falha ao atualizar queries automaticamente: {exc}"
        )
        if wb is not None:
            try:
                _save_workbook_robust(wb, path)
                _log("Planilha salva após recuperação do refresh.")
                refresh_ok = True
            except Exception as save_exc:
                _log(f"AVISO: Salvamento automático também falhou: {save_exc}")
        if not refresh_ok:
            _log("O merge na Base já foi salvo. Conclua o refresh manualmente no Excel.")
        if not refresh_ok:
            try:
                if excel is not None:
                    excel.Visible = True
            except Exception:
                pass
            try:
                _prompt_manual_refresh()
            except Exception:
                _log("AVISO: Não foi possível aguardar refresh manual — siga no Excel se necessário.")
    finally:
        if refresh_ok:
            _safe_excel_shutdown(wb, excel)
        elif excel is not None:
            try:
                if config.EXCEL_VISIBLE or is_reveal_requested():
                    excel.Visible = True
            except Exception:
                pass
        pythoncom.CoUninitialize()

    if not refresh_ok:
        _log(
            "AVISO: Queries não confirmadas como atualizadas. "
            "Verifique a planilha no Excel e salve se necessário."
        )
    # OneDrive às vezes remove/oculta a master logo após Quit do Excel.
    ensure_master_workbook(path, backup_path=backup_path, wait_s=12.0)
    return refresh_ok


def append_to_base(
    download_path: Path | str,
    target_path: Path | str | None = None,
    sheet_name: str | None = None,
    *,
    refresh_queries: bool | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> tuple[Path, MergeStats]:
    download_path = Path(download_path)
    target_path = Path(target_path or config.TARGET_EXCEL)
    sheet_name = sheet_name or config.TARGET_SHEET
    stats = MergeStats()

    _log(f"Export Power BI: {download_path}")
    _log(f"Merge na master: {target_path}")

    if not download_path.exists():
        raise FileNotFoundError(f"Arquivo exportado não encontrado: {download_path}")
    if not target_path.exists():
        raise FileNotFoundError(f"Planilha master não encontrada: {target_path}")

    _log(f"Lendo aba '{sheet_name}' da planilha master")
    df_base = _read_base_sheet(target_path, sheet_name)
    df_base = _normalize_dataframe(df_base)
    base_cols = list(df_base.columns)
    rows_before = len(df_base)

    _log(f"Lendo export")
    df_new = pd.read_excel(download_path, engine="openpyxl")
    df_new = _normalize_column_names(df_new)
    df_new = df_new.reindex(columns=base_cols)
    df_new = _normalize_dataframe(df_new)
    rows_imported = len(df_new)

    stats.warnings = validate_export_row_count(df_new, period_start, period_end)
    for warning in stats.warnings:
        _log(warning)

    combined, duplicates_removed = _dedupe_combined(df_base, df_new)
    rows_added = len(combined) - rows_before

    stats.rows_before = rows_before
    stats.rows_after = len(combined)
    stats.rows_added = rows_added
    stats.rows_imported = rows_imported
    stats.duplicates_removed = duplicates_removed

    _log(f"Linhas na Base antes: {rows_before}")
    _log(f"Linhas importadas: {rows_imported}")
    _log(f"Novas linhas únicas: {rows_added}")
    _log(f"Duplicatas removidas: {duplicates_removed}")
    _log(f"Total final na Base: {len(combined)}")
    _log(stats.status_message())

    if not should_persist_merge(rows_added, duplicates_removed):
        _log("Nenhuma linha nova ou atualizada — planilha master não alterada.")
        should_refresh = (
            config.REFRESH_QUERIES_AFTER_MERGE if refresh_queries is None else refresh_queries
        )
        if should_refresh:
            _log("Atualizando todas as queries e conexões do Excel...")
            if not refresh_workbook_queries(target_path):
                _log("Merge concluído; verifique o refresh das queries no Excel.")
        ensure_master_workbook(target_path, wait_s=8.0)
        return target_path, stats

    if rows_added == 0 and duplicates_removed > 0:
        _log(
            f"Atualizando {duplicates_removed} linha(s) existente(s) com dados do export Power BI."
        )

    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = config.BACKUPS_DIR / f"FALHAS_CRITICAS_MANUAL_{timestamp}.xlsx"
    shutil.copy2(target_path, backup_path)
    _log(f"Backup criado: {backup_path}")

    _log("Atualizando somente a aba Base no arquivo (preservando queries)...")
    _update_base_sheet_via_zip(target_path, sheet_name, combined)
    _log(f"Planilha master atualizada: {target_path}")

    should_refresh = (
        config.REFRESH_QUERIES_AFTER_MERGE if refresh_queries is None else refresh_queries
    )
    if should_refresh:
        _log("Atualizando todas as queries e conexões do Excel...")
        if not refresh_workbook_queries(target_path, backup_path=backup_path):
            _log("Merge concluído; verifique o refresh das queries no Excel.")
    else:
        ensure_master_workbook(target_path, backup_path=backup_path, wait_s=8.0)

    return target_path, stats
