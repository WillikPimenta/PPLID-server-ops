"""Cleanup duplicates and move selenium functions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 1. Move _baixar_arquivos_rotina from io.py to selenium_brflow.py
io_path = ROOT / "app/bots/rotina/io.py"
sel_path = ROOT / "app/bots/rotina/selenium_brflow.py"
io_text = io_path.read_text(encoding="utf-8")
start = io_text.index("def _baixar_arquivos_rotina(")
end = io_text.index("\n\ndef _obter_data_base_execucao(")
fn = io_text[start:end].rstrip()
io_path.write_text(io_text[:start] + io_text[end + 1:], encoding="utf-8")
sel_text = sel_path.read_text(encoding="utf-8")
if "_baixar_arquivos_rotina" not in sel_text:
    sel_path.write_text(sel_text.rstrip() + "\n\n\n" + fn + "\n", encoding="utf-8")
print("Moved _baixar_arquivos_rotina to selenium_brflow.py")

# 2. Trim duplicate blocks from confer.py (lines 269-557, 1-based)
conf_path = ROOT / "app/bots/rotina/tasks/confer.py"
lines = conf_path.read_text(encoding="utf-8").splitlines(keepends=True)
# find start/end by markers
start_idx = next(i for i, l in enumerate(lines) if l.startswith("def _juntar_confer_prod_tratados_dia"))
end_idx = next(i for i, l in enumerate(lines) if l.startswith("_CONFER_LOGIN_BUTTON_XPATH"))
new_lines = lines[:start_idx] + lines[end_idx:]
# remove duplicate xpath constants if already in header
text = "".join(new_lines)
if text.count("_CONFER_LOGIN_BUTTON_XPATH") > 1:
    first = text.index("_CONFER_LOGIN_BUTTON_XPATH")
    second = text.index("_CONFER_LOGIN_BUTTON_XPATH", first + 1)
    third = text.index("\n\n", second)
    fourth = text.index("\n\n", third + 2)
    text = text[:second] + text[fourth + 2:]
conf_path.write_text(text, encoding="utf-8")
print("Trimmed confer.py duplicates")

# 3. Light rotina __init__
init_path = ROOT / "app/bots/rotina/__init__.py"
init_path.write_text(
    '"""Pacote rotina — automação de relatórios BRFlow."""\n\n'
    'from app.bots.rotina.orchestration import executar_novo_bot, start, stop\n\n'
    '__all__ = ["start", "stop", "executar_novo_bot"]\n',
    encoding="utf-8",
)

# 4. Fix bot_rotina facade imports
facade = '''"""
Bot Rotina - Fachada de compatibilidade.

Implementação em app.bots.rotina.*
"""

from app.bots.rotina.constants import COLUNAS_MONITOR_UNIFICADO
from app.bots.rotina.io import _ler_csv_tratamento, _preparar_dataframe_para_parquet
from app.bots.rotina.orchestration import executar_novo_bot, start, stop
from app.bots.rotina.state import parar_event, set_progress_callback, set_status_callback
from app.bots.rotina.tasks.monitor import (
    _normalizar_df_monitor_sessoes,
    _pretratar_monitor_verifica_usuario,
    tratar_arquivo,
)
from app.bots.rotina.tasks.unificados import _juntar_monitor_tratado_com_monitor_confer_dia

__all__ = [
    "start",
    "stop",
    "executar_novo_bot",
    "parar_event",
    "set_status_callback",
    "set_progress_callback",
    "tratar_arquivo",
    "COLUNAS_MONITOR_UNIFICADO",
    "_juntar_monitor_tratado_com_monitor_confer_dia",
    "_normalizar_df_monitor_sessoes",
    "_preparar_dataframe_para_parquet",
    "_pretratar_monitor_verifica_usuario",
    "_ler_csv_tratamento",
]

if __name__ == "__main__":
    from app.bots.rotina.state import _set_status

    parar_event.clear()
    try:
        executar_novo_bot()
        _set_status("Robô completou com sucesso!")
    except KeyboardInterrupt:
        stop()
        _set_status("Robô interrompido pelo usuário")
    except Exception as exc:
        stop()
        _set_status(f"Erro: {exc}")
'''
(ROOT / "app/bots/bot_rotina.py").write_text(facade, encoding="utf-8")
print("Updated bot_rotina.py facade")

# 5. Add datetime import to selenium if missing
sel_text = sel_path.read_text(encoding="utf-8")
if "from datetime import datetime" not in sel_text:
    sel_text = sel_text.replace(
        "import time\nfrom pathlib import Path",
        "import time\nfrom datetime import datetime\nfrom pathlib import Path",
    )
    sel_path.write_text(sel_text, encoding="utf-8")

# 6. Remove _baixar_relatorio_retry from confer if duplicated in selenium - keep in confer for now
# Move _baixar_relatorio_retry to selenium_brflow
conf_text = conf_path.read_text(encoding="utf-8")
marker = "def _baixar_relatorio_retry_rotina("
if marker in conf_text:
    cs = conf_text.index(marker)
    ce = conf_text.index("\n\ndef _gerar_csv_sessoes_por_evento(", cs)
    retry_fn = conf_text[cs:ce].rstrip()
    conf_path.write_text(conf_text[:cs] + conf_text[ce + 1:], encoding="utf-8")
    sel_text = sel_path.read_text(encoding="utf-8")
    if "_baixar_relatorio_retry_rotina" not in sel_text:
        sel_path.write_text(sel_text.rstrip() + "\n\n\n" + retry_fn + "\n", encoding="utf-8")
    print("Moved _baixar_relatorio_retry_rotina to selenium_brflow.py")

print("Cleanup done.")
