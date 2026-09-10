"""Testes unitários dos helpers puros do bot Jira Suporte Claro."""

from app.bots.suporte_claro_jira.jira_ui import (
    _field_text_matches,
    _option_text_matches,
    _wizard_ready_for_next,
)
from app.bots.suporte_claro_jira.profiles import get_profile


class _WizardDriver:
    def __init__(self, project: str, issue_type: str):
        self.project = project
        self.issue_type = issue_type

    def find_element(self, by, value):
        raise NotImplementedError

    def find_elements(self, by, value):
        return []


def test_wizard_ready_for_next_when_fields_match(monkeypatch):
    profile = get_profile("planejamento")
    driver = _WizardDriver("Planejamento IDF (PPLID)", "Tarefa")

    def fake_field_already_selected(drv, field_id, *hints):
        if field_id == "project-field":
            return "pplid" in " ".join(hints).lower() or "planejamento" in " ".join(hints).lower()
        if field_id == "issuetype-field":
            return "tarefa" in " ".join(hints).lower()
        return False

    monkeypatch.setattr(
        "app.bots.suporte_claro_jira.jira_ui._field_already_selected",
        fake_field_already_selected,
    )
    assert _wizard_ready_for_next(driver, profile) is True


def test_option_text_matches_informacao():
    assert _option_text_matches("Informação", "Informação")
    assert _option_text_matches("Informação", "Informacao")
    assert _option_text_matches("Informacao", "Informação")
    assert not _option_text_matches("Informação", "Nenhum")


def test_field_text_matches_project():
    assert _field_text_matches("Planejamento IDF (PPLID)", "pplid", "Planejamento IDF")
    assert not _field_text_matches("Nenhum", "pplid")
