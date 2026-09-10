"""Testes de situação ativo/inativo na listagem BRFlow (helpers + retomada)."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import app.config as cfg
import app.bots.replicacao_aud_planning as rap


def _load_bot_module():
    path = Path(__file__).resolve().parents[1] / "app" / "bots" / "bot_replicacao_aud.py"
    spec = importlib.util.spec_from_file_location("bot_replicacao_aud", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeTd:
    def __init__(self, text="", attrs=None):
        self._text = text
        self._attrs = attrs or {}

    @property
    def text(self):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)


def test_classificar_situacao_btn_status_title():
    bot = _load_bot_module()
    assert bot._classificar_situacao_texto("Ativo") == "ATIVO"
    assert bot._classificar_situacao_texto("Inativo") == "INATIVO"


def test_regra_filtro_js_ocultar_linha():
    bot = _load_bot_module()
    assert bot._linha_deve_ocultar_por_status_titulo("Ativo") is False
    assert bot._linha_deve_ocultar_por_status_titulo("Inativo") is True
    assert bot._linha_deve_ocultar_por_status_titulo(None) is True
    assert bot._linha_deve_ocultar_por_status_titulo("") is True


def test_filtro_js_remove_linhas_inativas():
    bot = _load_bot_module()
    import inspect

    src = inspect.getsource(bot._filtrar_listagem_apenas_ativos_js)
    assert "tr.remove()" in src
    assert 'display = "none"' not in src
    assert "removidas" in src
    assert "mantidas" in src


def test_extrair_situacao_mock_btn_status():
    bot = _load_bot_module()

    class _Btn:
        def __init__(self, title):
            self._title = title

        def get_attribute(self, name):
            if name == "title":
                return self._title
            return ""

    class _Linha:
        def find_element(self, by, value):
            return _Btn("Ativo")

        def find_elements(self, *args, **kwargs):
            return []

    assert bot._extrair_texto_situacao_linha(_Linha()) == "Ativo"
    assert bot._classificar_situacao_texto(bot._extrair_texto_situacao_linha(_Linha())) == "ATIVO"


def test_classificar_situacao_texto():
    bot = _load_bot_module()
    assert bot._classificar_situacao_texto("Ativo") == "ATIVO"
    assert bot._classificar_situacao_texto("Inativo") == "INATIVO"
    assert bot._classificar_situacao_texto("INATIVO") == "INATIVO"
    assert bot._classificar_situacao_texto("") == "DESCONHECIDO"


def test_extrair_texto_celula_atributo_data_html():
    bot = _load_bot_module()
    td = _FakeTd(
        text="",
        attrs={"data-html-nom_workflow_origem": "Risk Manager - PICPAY"},
    )
    assert bot._extrair_texto_celula_replicacao(td) == "Risk Manager - PICPAY"


def test_extrair_texto_celula_prefere_texto_visivel():
    bot = _load_bot_module()
    td = _FakeTd(
        text="WF Visível",
        attrs={"data-html-nom_workflow_origem": "WF Atributo"},
    )
    assert bot._extrair_texto_celula_replicacao(td) == "WF Visível"


def test_normalizar_workflow_remove_acentos():
    assert rap._normalizar_workflow("Novo Pré Venda") == rap._normalizar_workflow("Novo Pre Venda")


def test_listagem_nao_estabiliza_enquanto_contagem_sobe():
    bot = _load_bot_module()
    _, repeticoes, pronta = bot._listagem_pronta_para_ler(25, 250, 1, 2, 1, 800)
    assert repeticoes == 0
    assert pronta is False


def test_listagem_estabiliza_quando_atinge_alvo_paginador():
    bot = _load_bot_module()
    _, repeticoes, pronta = bot._listagem_pronta_para_ler(799, 800, 0, 2, 1, 800)
    assert repeticoes == 0
    assert pronta is False
    _, repeticoes, pronta = bot._listagem_pronta_para_ler(800, 800, 0, 2, 1, 800)
    assert repeticoes == 1
    assert pronta is False
    _, repeticoes, pronta = bot._listagem_pronta_para_ler(800, 800, 1, 2, 1, 800)
    assert repeticoes == 2
    assert pronta is True


def test_listagem_aceita_estavel_abaixo_do_alvo_paginador():
    bot = _load_bot_module()
    _, _, pronta = bot._listagem_pronta_para_ler(
        250, 250, 6, 2, 1, 346, polls_estavel_sem_alvo=8
    )
    assert pronta is False
    _, _, pronta = bot._listagem_pronta_para_ler(
        250, 250, 7, 2, 1, 346, polls_estavel_sem_alvo=8
    )
    assert pronta is True


def test_resumir_duplicata_ativo_inativo():
    bot = _load_bot_module()
    linhas = [
        {"_wf_key": "wf x", "workflow": "WF X", "situacao": "INATIVO"},
        {"_wf_key": "wf x", "workflow": "WF X", "situacao": "ATIVO"},
    ]
    resumo = bot._resumir_linhas_por_workflow(linhas)
    assert resumo["wf x"]["tem_ativo"] is True
    assert resumo["wf x"]["tem_inativo"] is True
    assert resumo["wf x"]["linhas_ativas"] == 1
    assert resumo["wf x"]["linhas_inativas"] == 1
    assert bot._workflow_tem_linha_ativa_brflow(resumo, "WF X") is True
    assert bot._eh_workflow_apenas_inativo_brflow(resumo, "WF X") is False


def test_apenas_inativo():
    bot = _load_bot_module()
    linhas = [{"_wf_key": "wf y", "workflow": "WF Y", "situacao": "INATIVO"}]
    resumo = bot._resumir_linhas_por_workflow(linhas)
    assert bot._workflow_tem_linha_ativa_brflow(resumo, "WF Y") is False
    assert bot._eh_workflow_apenas_inativo_brflow(resumo, "WF Y") is True


def test_desconhecido_nao_bloqueia_como_inativo():
    bot = _load_bot_module()
    linhas = [{"_wf_key": "wf z", "workflow": "WF Z", "situacao": "DESCONHECIDO"}]
    resumo = bot._resumir_linhas_por_workflow(linhas)
    assert bot._eh_workflow_apenas_inativo_brflow(resumo, "WF Z") is False


def test_retomada_exclui_inativo():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        proto = base / "protocolos"
        resumo = base / "resumo"
        proto.mkdir()
        resumo.mkdir()

        orig_proto = cfg.PASTA_REPLICACAO_AUD_PROTOCOLOS
        orig_resumo = cfg.PASTA_REPLICACAO_AUD_RESUMO
        rap.PASTA_REPLICACAO_AUD_PROTOCOLOS = proto
        rap.PASTA_REPLICACAO_AUD_RESUMO = resumo

        run_id = "20260527_inativo"
        pasta = proto / run_id
        pasta.mkdir()
        csv_a = pasta / "Workflow_A.csv"
        csv_b = pasta / "Workflow_B.csv"
        csv_a.write_text("P100\n", encoding="utf-8")
        csv_b.write_text("P200\n", encoding="utf-8")
        estado = {
            "run_id": run_id,
            "pasta_protocolos": str(pasta),
            "workflows": {
                "Workflow A": {"status": "INATIVO", "csv": str(csv_a), "atualizado_em": ""},
                "Workflow B": {"status": "PENDENTE", "csv": str(csv_b), "atualizado_em": ""},
            },
        }
        (resumo / f"execucao_{run_id}.json").write_text(json.dumps(estado), encoding="utf-8")

        plano = rap.carregar_plano_por_run_id(run_id, {"apenas_pendentes": True})
        assert "Workflow B" in plano.workflows
        assert "Workflow A" not in plano.workflows

        rap.PASTA_REPLICACAO_AUD_PROTOCOLOS = orig_proto
        rap.PASTA_REPLICACAO_AUD_RESUMO = orig_resumo


def main():
    test_classificar_situacao_btn_status_title()
    test_regra_filtro_js_ocultar_linha()
    test_filtro_js_remove_linhas_inativas()
    test_extrair_situacao_mock_btn_status()
    test_classificar_situacao_texto()
    test_extrair_texto_celula_atributo_data_html()
    test_extrair_texto_celula_prefere_texto_visivel()
    test_normalizar_workflow_remove_acentos()
    test_listagem_nao_estabiliza_enquanto_contagem_sobe()
    test_listagem_estabiliza_quando_atinge_alvo_paginador()
    test_listagem_aceita_estavel_abaixo_do_alvo_paginador()
    test_resumir_duplicata_ativo_inativo()
    test_apenas_inativo()
    test_desconhecido_nao_bloqueia_como_inativo()
    test_retomada_exclui_inativo()
    print("OK: testes situacao BRFlow passaram")


if __name__ == "__main__":
    main()
