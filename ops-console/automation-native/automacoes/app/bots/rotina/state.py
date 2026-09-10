"""Estado de execução e callbacks BotRuntime."""
from __future__ import annotations

import logging
import time

from app.core.bot_runtime import BotRuntime
from app.bots.rotina.constants import TAREFAS_DEMANDAS, TASK_TO_ROTINA_DESCRICAO

log = logging.getLogger("robots.bot_rotina")

_runtime = BotRuntime(mode="rotina")
parar_event = _runtime.parar_event
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress
_reset_progress_state = _runtime.reset_progress_state

timing_start = {}

exec_summary = {
    "arquivos": [],
    "total_duplicatas_merge": 0,
    "total_duplicatas_internas": 0,
    "total_linhas_finais": 0,
    "erros": [],
    "task_status": {},
}

def _reset_exec_summary():
    """Reseta o resumo consolidado da execução atual."""
    exec_summary["arquivos"] = []
    exec_summary["total_duplicatas_merge"] = 0
    exec_summary["total_duplicatas_internas"] = 0
    exec_summary["total_linhas_finais"] = 0
    exec_summary["erros"] = []
    exec_summary["task_status"] = {}


def _inicializar_status_tarefas(tarefas_final: list):
    """Inicializa status das demandas da tabela Teams conforme tarefas selecionadas."""
    task_status = {}
    for task_id in TAREFAS_DEMANDAS:
        if task_id in tarefas_final:
            task_status[task_id] = {"estado": "pendente", "obs": ""}
        else:
            task_status[task_id] = {"estado": "nao_executado", "obs": ""}
    exec_summary["task_status"] = task_status


def _registrar_status_tarefa(task_id: str, ok: bool, obs: str = ""):
    """Registra sucesso ou falha de uma tarefa para a tabela de demandas."""
    if task_id not in exec_summary.get("task_status", {}):
        return
    exec_summary["task_status"][task_id] = {
        "estado": "ok" if ok else "erro",
        "obs": str(obs or ("OK" if ok else "erro"))[:80],
    }


def _task_id_por_descricao_rotina(descricao: str):
    """Mapeia descrição BRBR-4467 ao task_id correspondente."""
    for task_id, desc in TASK_TO_ROTINA_DESCRICAO.items():
        if desc == descricao:
            return task_id
    return None


def _extra_obs_rules_rotina(valor: str):
    if valor.lower().startswith("download incompleto"):
        return valor[:60]
    if valor.lower().startswith("erro tarefa"):
        return "Falha durante a execução da tarefa"
    if "sem arquivo" in valor.lower():
        return "Arquivo não encontrado"
    return None


def _humanizar_observacao(texto: str) -> str:
    return humanizar_observacao(
        texto,
        obs_map=OBS_HUMANIZADA,
        extra_rules=_extra_obs_rules_rotina,
    )


def _teams_notificacao_habilitada(settings=None) -> bool:
    """Teams só no modo agendado; execução imediata suprime notificações."""
    s = settings or {}
    if s.get("executar_imediatamente"):
        return False
    if os.getenv("ROTINA_EXECUTAR_IMEDIATAMENTE", "").strip() == "1":
        return False
    return True


def _build_adaptive_card_demandas(erros: list = None, status: str = "sucesso") -> dict:
    agora = datetime.now().strftime("%d/%m/%Y às %H:%M")
    return build_status_card(
        titulo_card="Rotina diária — status das demandas",
        subtitulo=f"Atualizado em {agora}",
        demandas_tabela=DEMANDAS_TABELA,
        task_status=exec_summary["task_status"],
        erros=erros,
        obs_map=OBS_HUMANIZADA,
        extra_obs_rules=_extra_obs_rules_rotina,
    )


def _registrar_erro(mensagem: str):
    """Acumula erros para serem incluídos na notificação final."""
    exec_summary["erros"].append(str(mensagem))


def _registrar_arquivo_resumo(nome_arquivo: str, linhas_finais: int, duplicatas_merge: int = 0, tipo: str = "novo"):
    """Registra estatísticas de um arquivo processado para resumo final."""
    exec_summary["arquivos"].append({
        "nome": nome_arquivo,
        "linhas_finais": max(0, int(linhas_finais or 0)),
        "duplicatas_merge": max(0, int(duplicatas_merge or 0)),
        "tipo": tipo,
    })
    exec_summary["total_duplicatas_merge"] += max(0, int(duplicatas_merge or 0))
    exec_summary["total_linhas_finais"] += max(0, int(linhas_finais or 0))


def _registrar_duplicatas_internas(qtd: int):
    """Acumula duplicatas removidas internamente durante a consolidação do dia."""

def _timing_start_step(step_name):
    """Inicia cronômetro para uma etapa."""
    timing_start[step_name] = time.time()


def _timing_end_step(step_name):
    """Encerra cronômetro e registra duração."""
    if step_name in timing_start:
        elapsed = time.time() - timing_start[step_name]
        log.info(f"✓ {step_name}: {elapsed:.2f}s")
        del timing_start[step_name]
        return elapsed
    return 0
