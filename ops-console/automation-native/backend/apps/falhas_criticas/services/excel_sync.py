# -*- coding: utf-8 -*-
import pandas as pd
from datetime import datetime, date
from pathlib import Path
from django.db import transaction

import report_falhas.config_report as cfg
from report_falhas.contestacoes import _read_contestacoes, get_contest_state
from report_falhas.falhas_removidas import get_removidas_state
from report_falhas.data_base import read_base, build_maps_nome_e_tempos
from report_falhas.hc_maps import (
    build_atividade_atual_hc_map,
    build_team_categoria_map,
    build_team_hc_map,
    build_turno_hc_map,
    classify_fn_fp_from_novo_cenario,
)
from report_falhas.team_category import CATEGORIA_NAO_CLASSIFICADO
from report_falhas.training_utils import training_prefix, training_situacao_calculada
from report_falhas.io.data_loader import read_hc, read_suporte, read_treinamentos, norm_matricula, safe_str, safe_to_datetime, compute_conformidade_suporte

from apps.falhas_criticas.models import FalhasAgent, Contestation, Failure, Support, Training
from apps.falhas_criticas.services.sync_helpers import (
    build_name2mat_from_hc,
    looks_like_matricula,
    resolve_agent_from_suporte_row,
)
from apps.falhas_criticas.services.sync_stats import build_sync_stats, build_sync_stats_empty
from apps.falhas_criticas.services.localidade import canonicalize_localidade


def build_hc_field_map(dfh: pd.DataFrame, field: str) -> dict[str, str]:
    """Último valor de um campo HC por matrícula (registro mais recente)."""
    out: dict[str, str] = {}
    if dfh is None or dfh.empty or field not in dfh.columns:
        return out
    dfx = dfh.copy()
    if 'mat_norm' not in dfx.columns and 'matricula_agente' in dfx.columns:
        dfx['mat_norm'] = dfx['matricula_agente'].apply(norm_matricula)
    dfx[field] = dfx[field].apply(safe_str)
    dfx = dfx[dfx[field] != ''].copy()
    if dfx.empty:
        return out
    if 'data_inicial' in dfx.columns and (not pd.api.types.is_datetime64_any_dtype(dfx['data_inicial'])):
        dfx['data_inicial'] = safe_to_datetime(dfx['data_inicial'])
    if 'id' in dfx.columns:
        dfx['_id_num'] = pd.to_numeric(dfx['id'], errors='coerce').fillna(-1)
    else:
        dfx['_id_num'] = -1
    dfx = dfx.dropna(subset=['mat_norm']).copy()
    if dfx.empty:
        return out
    sort_cols = ['mat_norm'] + (['data_inicial'] if 'data_inicial' in dfx.columns else []) + ['_id_num']
    dfx = dfx.sort_values(sort_cols, ascending=True)
    last = dfx.groupby('mat_norm')[field].last()
    return {str(k): safe_str(v) for k, v in last.items()}


def build_localidade_hc_map(dfh: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    if dfh is None or dfh.empty:
        return out
    dfx = dfh.copy()
    if 'mat_norm' not in dfx.columns and 'matricula_agente' in dfx.columns:
        dfx['mat_norm'] = dfx['matricula_agente'].apply(norm_matricula)
    loc_col = 'localidade' if 'localidade' in dfx.columns else None
    if not loc_col:
        return out
    if 'data_inicial' in dfx.columns and (not pd.api.types.is_datetime64_any_dtype(dfx['data_inicial'])):
        dfx['data_inicial'] = safe_to_datetime(dfx['data_inicial'])
    dfx = dfx.dropna(subset=['mat_norm']).copy()
    if dfx.empty:
        return out
    sort_cols = ['mat_norm'] + (['data_inicial'] if 'data_inicial' in dfx.columns else [])
    dfx = dfx.sort_values(sort_cols, ascending=True)
    last = dfx.groupby('mat_norm')[loc_col].last()
    out = {str(k): canonicalize_localidade(v) for k, v in last.items()}
    return out


def clean_date_to_db(val):
    if pd.isna(val) or val is None:
        return None
    if isinstance(val, pd.Timestamp):
        return val.date()
    if isinstance(val, (datetime, date)):
        return val
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return None


def _clip(val, max_len: int) -> str:
    s = safe_str(val)
    if max_len > 0 and len(s) > max_len:
        return s[:max_len]
    return s


def sync_excel_to_db(path=None, user=None):
    """Sincroniza o Excel com PostgreSQL. Retorna (sucesso, stats)."""
    from apps.falhas_criticas.services.excel_path import require_excel_source_path
    excel_path = require_excel_source_path(path)
    print(f"Iniciando sincronização a partir de: {excel_path}")

    if not Path(excel_path).exists():
        msg = f"Arquivo Excel não encontrado: {excel_path}"
        print(msg)
        raise FileNotFoundError(msg)

    try:
        df_base = read_base(excel_path, cfg.ABA_BASE)
        dfh = read_hc(excel_path, cfg.ABA_HC)
        df_suporte = read_suporte(excel_path, cfg.ABA_SUPORTE)
        df_trein = read_treinamentos(excel_path, 'Treinamentos')
        df_ci, df_ce = _read_contestacoes(excel_path)
    except PermissionError:
        msg = (
            "Não foi possível ler o Excel — o arquivo provavelmente está aberto no Excel. "
            "Feche a planilha e tente novamente."
        )
        print(msg)
        raise PermissionError(msg)
    except OSError as e:
        if getattr(e, 'errno', None) == 13 or 'Permission denied' in str(e):
            msg = (
                "Não foi possível ler o Excel — feche a planilha no Excel (e no OneDrive) e tente novamente."
            )
            print(msg)
            raise PermissionError(msg) from e
        raise

    if df_base.empty:
        print("Planilha Base de Falhas está vazia. Cancelando sincronização.")
        return False, build_sync_stats_empty("Planilha Base de Falhas vazia.")

    contest_resumo, _, _ = get_contest_state()
    removidas_resumo, _, _ = get_removidas_state()
    hc_rows_read = int(len(dfh)) if dfh is not None and not dfh.empty else 0
    support_rows_read = int(len(df_suporte)) if df_suporte is not None and not df_suporte.empty else 0
    training_rows_read = int(len(df_trein)) if df_trein is not None and not df_trein.empty else 0
    contest_rows_read = int(len(df_ci) if df_ci is not None else 0) + int(len(df_ce) if df_ce is not None else 0)

    mats_base = set(df_base[cfg.COL_MATRICULA].apply(safe_str).unique())
    mats_hc = set(dfh['matricula_agente'].apply(safe_str).unique()) if 'matricula_agente' in dfh.columns else set()
    all_mats_norm = {
        norm_matricula(m)
        for m in (mats_base | mats_hc)
        if looks_like_matricula(norm_matricula(m))
    }

    nome_map, tempo_casa_map, tempo_etapa_map = build_maps_nome_e_tempos(df_base, dfh)
    atividade_map = build_atividade_atual_hc_map(dfh)
    turno_map = build_turno_hc_map(dfh)
    localidade_map = build_localidade_hc_map(dfh)
    leader_map = build_hc_field_map(dfh, 'leader_nome')
    job_title_map = build_hc_field_map(dfh, 'job_title')
    team_map = build_team_hc_map(dfh)
    team_categoria_map = build_team_categoria_map(dfh)

    admissao_map = {}
    if 'matricula_agente' in dfh.columns and 'data_admissao' in dfh.columns:
        dfh_sorted = dfh.sort_values('data_inicial', ascending=True)
        for _, row in dfh_sorted.iterrows():
            m_norm = norm_matricula(row['matricula_agente'])
            if m_norm:
                adm = clean_date_to_db(row['data_admissao'])
                if adm:
                    admissao_map[m_norm] = adm

    stale_count = 0
    with transaction.atomic():
        Failure.objects.all().delete()
        Support.objects.all().delete()
        Training.objects.all().delete()
        Contestation.objects.all().delete()

        existing_agents = {a.matricula_norm: a for a in FalhasAgent.objects.all()}
        agents_to_update = []
        agents_to_create = []

        for m_norm in all_mats_norm:
            name = safe_str(nome_map.get(m_norm, ''))
            adm = admissao_map.get(m_norm, None)
            agent = existing_agents.get(m_norm)
            fields = {
                'name': name or (agent.name if agent else ''),
                'admissao_date': adm or (agent.admissao_date if agent else None),
                'tempo_casa': safe_str(tempo_casa_map.get(m_norm, '—')),
                'tempo_etapa': safe_str(tempo_etapa_map.get(m_norm, '—')),
                'atividade_atual': safe_str(atividade_map.get(m_norm, '')),
                'turno_atual': safe_str(turno_map.get(m_norm, '')),
                'localidade': canonicalize_localidade(localidade_map.get(m_norm, '')),
                'leader_nome': safe_str(leader_map.get(m_norm, '')),
                'job_title': safe_str(job_title_map.get(m_norm, '')),
                'team': safe_str(team_map.get(m_norm, '')),
                'team_categoria': safe_str(
                    team_categoria_map.get(m_norm, CATEGORIA_NAO_CLASSIFICADO)
                ),
            }
            if agent:
                for k, v in fields.items():
                    setattr(agent, k, v)
                agents_to_update.append(agent)
            else:
                agents_to_create.append(FalhasAgent(matricula_norm=m_norm, **fields))

        if agents_to_create:
            FalhasAgent.objects.bulk_create(agents_to_create)
        if agents_to_update:
            FalhasAgent.objects.bulk_update(
                agents_to_update,
                ['name', 'admissao_date', 'tempo_casa', 'tempo_etapa', 'atividade_atual', 'turno_atual', 'localidade', 'leader_nome', 'job_title'],
            )

        db_agents = {a.matricula_norm: a for a in FalhasAgent.objects.all()}
        name2mat = build_name2mat_from_hc(dfh)

        failures_to_create = []
        for _, row in df_base.iterrows():
            m_raw = safe_str(row.get(cfg.COL_MATRICULA, ''))
            m_norm = norm_matricula(m_raw)
            agent_obj = db_agents.get(m_norm) if m_norm else None
            data_an = clean_date_to_db(row.get('Data de Análise'))
            if not data_an:
                continue

            novo_cen = safe_str(row.get('Novo cenário', ''))
            cenario_unif = safe_str(row.get('Cenário Unificado', '')) or safe_str(row.get('Cenário', '')) or novo_cen
            loc = canonicalize_localidade(row.get('Localidade', 'Geral'))
            if agent_obj and not agent_obj.localidade and loc:
                agent_obj.localidade = loc

            failures_to_create.append(Failure(
                protocolo=safe_str(row.get('Protocolo', '')),
                data_analise=data_an,
                data_auditoria=clean_date_to_db(row.get('Data Auditoria')),
                etapa=safe_str(row.get('Etapa', '')),
                categoria=safe_str(row.get('Categoria falha', '')),
                cenario=cenario_unif,
                novo_cenario=novo_cen or cenario_unif,
                localidade=loc,
                cliente=safe_str(row.get('Cliente', '')),
                workflow=safe_str(row.get('Workflow', '')),
                dificuldade=safe_str(row.get('Nível de Dificuldade', '')),
                tipo_documento=safe_str(row.get('Tipo de documento', '')),
                uf_documento=safe_str(row.get('UF do documento', '')),
                tipo_falha=classify_fn_fp_from_novo_cenario(novo_cen or cenario_unif),
                modulo=safe_str(row.get('Módulo', '')),
                agent=agent_obj,
            ))

        if failures_to_create:
            Failure.objects.bulk_create(failures_to_create)

        supports_to_create = []
        for _, row in df_suporte.iterrows():
            data_sup = clean_date_to_db(row.get('Data'))
            protocolo = safe_str(row.get('Protocolo', ''))
            # Linhas sem data/protocolo são lixo de export — não poluem KPIs do período.
            if not data_sup and not protocolo:
                continue
            agent_obj, nome_raw, m_norm = resolve_agent_from_suporte_row(row, name2mat, db_agents)
            loc = canonicalize_localidade(
                row.get('Localidade solicitante', '') or (agent_obj.localidade if agent_obj else '')
            )
            conf_regra = safe_str(row.get('Conformidade (Regra)', ''))
            if not conf_regra:
                try:
                    conf_regra = compute_conformidade_suporte(row)
                except Exception:
                    conf_regra = safe_str(row.get('Conformidade', ''))
            supports_to_create.append(Support(
                protocolo=_clip(protocolo, 100),
                data=data_sup,
                cliente=_clip(row.get('Cliente', ''), 255),
                workflow=_clip(row.get('Workflow', ''), 255),
                tipo_solicitacao=_clip(row.get('Tipo de solicitação', ''), 255),
                conformidade=_clip(row.get('Conformidade', ''), 100),
                conformidade_regra=_clip(conf_regra, 100),
                dificuldade=_clip(row.get('Grau de dificuldade', ''), 100),
                critico=_clip(row.get('Crítico', ''), 100),
                uf_emissao=_clip(row.get('UF de emissão', ''), 50),
                localidade=_clip(loc, 100),
                agente_nome_planilha=_clip(nome_raw, 255),
                duvida=safe_str(row.get('Dúvida', '')),
                conclusao=safe_str(row.get('Conclusão', '')),
                lider_solicitante=_clip(row.get('Líder solicitante', ''), 255),
                tipo_documento=_clip(row.get('Tipo de documento', ''), 255),
                agent=agent_obj,
            ))

        if supports_to_create:
            Support.objects.bulk_create(supports_to_create)

        trainings_to_create = []
        ref_today = date.today()
        if df_trein is not None and not df_trein.empty:
            for _, row in df_trein.iterrows():
                mat = norm_matricula(row.get('Agent: UserLanID', ''))
                agent_obj = db_agents.get(mat) if mat else None
                titulo = safe_str(row.get('Event: EventTitle', ''))
                prefix = training_prefix(titulo)
                sit_calc = training_situacao_calculada(row, ref_today)
                trainings_to_create.append(Training(
                    titulo=titulo,
                    tipo_acao=titulo[:255],
                    tipo_acao_prefixo=prefix[:255],
                    status=safe_str(row.get('Status', '')),
                    situacao=sit_calc or safe_str(row.get('Status', '')),
                    situacao_calculada=sit_calc,
                    data_limite=clean_date_to_db(row.get('Event:EventDeadline')),
                    data_atribuicao=clean_date_to_db(row.get('AssignmentDate')),
                    data_assinatura=clean_date_to_db(row.get('SignatureDate')),
                    data_inicio_sessao=clean_date_to_db(row.get('Session: StartDate')),
                    data_fim_sessao=clean_date_to_db(row.get('Session: FinalDate')),
                    localidade=canonicalize_localidade(agent_obj.localidade if agent_obj else ''),
                    matricula=mat or '',
                    nome_agente=safe_str(row.get('Agent', '')) or (agent_obj.name if agent_obj else ''),
                ))
        if trainings_to_create:
            Training.objects.bulk_create(trainings_to_create)

        def _sync_contest(df_cont, fonte):
            items = []
            if df_cont is None or df_cont.empty:
                return items
            for _, row in df_cont.iterrows():
                colab = safe_str(row.get('Colaborador', ''))
                m_norm = norm_matricula(colab)
                agent_obj = db_agents.get(m_norm) if m_norm else None
                dt = row.get('__DATA_REF__', row.get('Data'))
                items.append(Contestation(
                    protocolo=safe_str(row.get('Protocolo', '')),
                    data=clean_date_to_db(dt),
                    fonte=fonte,
                    status=safe_str(row.get('Status', '')),
                    localidade=canonicalize_localidade(
                        row.get('Cidade', '') or (agent_obj.localidade if agent_obj else '')
                    ),
                    agent=agent_obj,
                ))
            return items

        contest_items = _sync_contest(df_ci, 'Interna') + _sync_contest(df_ce, 'Externa')
        if contest_items:
            Contestation.objects.bulk_create(contest_items)

        stale_agents = FalhasAgent.objects.exclude(matricula_norm__in=all_mats_norm)
        stale_count = stale_agents.count()
        if stale_count:
            stale_agents.delete()
            print(f"Removidos {stale_count} agente(s) inválido(s) ou fora da planilha.")

    stats = build_sync_stats(
        contest_resumo=contest_resumo,
        removidas_resumo=removidas_resumo,
        df_base_len=int(len(df_base)),
        failures_persisted=len(failures_to_create),
        hc_rows_read=hc_rows_read,
        agents_persisted=len(all_mats_norm),
        support_rows_read=support_rows_read,
        support_persisted=len(supports_to_create),
        training_rows_read=training_rows_read,
        training_persisted=len(trainings_to_create),
        contest_rows_read=contest_rows_read,
        contest_persisted=len(contest_items),
        extra_warnings=(
            [f"Removidos {stale_count} agente(s) obsoletos fora da planilha."]
            if stale_count
            else None
        ),
    )

    print("Sincronização concluída com sucesso!")
    return True, stats
