from report_falhas.render.pages.suporte import build_suporte_page_html
from report_falhas.render.pages.ult3m import (
    build_ult3m_agent_table, build_ult3m_training_focus_html,
    format_html_ultimos_3_meses, ult4m_frames, _diagnostico_bloco_html,
    _topn_join_counts, _resumo_doc_uf,
)
from report_falhas.render.pages.contestacoes import (
    build_contestacoes_page_html, build_falhas_retiradas_page_html,
    build_falhas_removidas_page_html,
)
from report_falhas.render.pages.treinamentos import build_treinamentos_tracking_page_html, _build_mats_ativos_hc
