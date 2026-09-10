"""Valida refatoração bot_rotina: funções críticas vs backup original."""
from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "app" / "bots" / "bot_rotina.py.bak"

# name -> module path for relocated functions
RELOCATED = {
    "tratar_arquivo": "app.bots.rotina.tasks.monitor",
    "tratar_produtividade": "app.bots.rotina.tasks.produtividade",
    "_pretratar_monitor_verifica_usuario": "app.bots.rotina.tasks.monitor",
    "_normalizar_df_monitor_sessoes": "app.bots.rotina.tasks.monitor",
    "_juntar_monitor_tratado_com_monitor_confer_dia": "app.bots.rotina.tasks.unificados",
    "_juntar_confer_prod_tratados_dia": "app.bots.rotina.tasks.unificados",
    "_aplicar_limpeza_bi": "app.bots.rotina.io",
    "MergeInteligente": "app.bots.rotina.csv_merge",
    "CSVReader": "app.bots.rotina.csv_merge",
    "_baixar_e_combinar_rotinas": "app.bots.rotina.tasks.detalhado",
    "_baixar_produtividade_d1": "app.bots.rotina.tasks.produtividade",
    "_baixar_monitor_eventos_d1": "app.bots.rotina.tasks.monitor",
    "_baixar_relatorio_producao_confer": "app.bots.rotina.tasks.confer",
    "log_eventos": "app.bots.rotina.tasks.confer",
    "start": "app.bots.rotina.orchestration",
    "executar_novo_bot": "app.bots.rotina.orchestration",
}


def _source_from_backup(name: str) -> str | None:
    if not BACKUP.exists():
        return None
    tree = ast.parse(BACKUP.read_text(encoding="utf-8-sig"))
    lines = BACKUP.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return "".join(lines[node.lineno - 1 : node.end_lineno])
    return None


def _normalize(src: str) -> str:
    return "\n".join(line.rstrip() for line in src.strip().splitlines())


def main() -> int:
    errors: list[str] = []
    ok = 0

    for name, mod_path in RELOCATED.items():
        backup_src = _source_from_backup(name)
        if backup_src is None:
            errors.append(f"{name}: não encontrado no backup")
            continue
        mod = importlib.import_module(mod_path)
        obj = getattr(mod, name)
        new_src = inspect.getsource(obj)
        if _normalize(backup_src) == _normalize(new_src):
            ok += 1
            print(f"  OK  {name} ({mod_path}) — idêntico ao backup")
        else:
            # allow minor whitespace/doc diffs; compare AST body
            try:
                b_tree = ast.parse(backup_src).body[0]
                n_tree = ast.parse(new_src).body[0]
                if ast.dump(b_tree, include_attributes=False) == ast.dump(n_tree, include_attributes=False):
                    ok += 1
                    print(f"  OK  {name} ({mod_path}) — AST equivalente")
                else:
                    errors.append(f"{name}: corpo diferente do backup ({mod_path})")
                    print(f"  FAIL {name} — corpo alterado")
            except Exception as exc:
                errors.append(f"{name}: erro comparando AST: {exc}")

    print(f"\nComparação backup: {ok}/{len(RELOCATED)} OK")
    if errors:
        print("\nProblemas:")
        for e in errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
