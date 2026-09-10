from django.test import SimpleTestCase, override_settings

from apps.planejamento_demandas.services.projects import (
    build_team_jql,
    build_team_terminal_jql,
    issue_matches_team_queue,
)


@override_settings(
    JIRA_DEMANDAS_PROJECT_KEYS="PPLID,ANTIFRAUDE,Qualidade ID&F",
    JIRA_DEMANDAS_TEAM_LAN_IDS="C93233A,C93123A",
    JIRA_STATUS_CONCLUIDO="Resolvida,Done,Closed",
    JIRA_DEMANDAS_STATUS_CANCELADO="Cancelada,Cancelado",
)
class TeamJqlTests(SimpleTestCase):
    def test_build_team_jql(self):
        jql = build_team_jql()
        self.assertIn("project in (PPLID, ANTIFRAUDE, \"Qualidade ID&F\")", jql)
        self.assertIn("assignee in (C93233A, C93123A)", jql)
        self.assertIn("reporter in (C93233A, C93123A)", jql)
        self.assertIn("statusCategory != Done", jql)
        self.assertIn("ORDER BY Rank ASC", jql)

    def test_build_team_jql_inactive_included(self):
        jql = build_team_jql(active_only=False)
        self.assertNotIn("statusCategory", jql)

    def test_build_team_terminal_jql(self):
        jql = build_team_terminal_jql()
        self.assertIn("statusCategory = Done", jql)
        self.assertIn("assignee in (C93233A, C93123A)", jql)
        self.assertIn("updated >= -", jql)

    @override_settings(JIRA_DEMANDAS_JQL_OPEN_CLAUSE="resolution is EMPTY")
    def test_build_team_jql_custom_open_clause(self):
        jql = build_team_jql()
        self.assertIn("resolution is EMPTY", jql)
        self.assertNotIn("statusCategory", jql)

    def test_issue_matches_team(self):
        self.assertTrue(
            issue_matches_team_queue(
                project_key="PPLID",
                assignee_username="C93233A",
                reporter_username="",
            )
        )
        self.assertFalse(
            issue_matches_team_queue(
                project_key="PPLID",
                assignee_username="OTHER",
                reporter_username="",
            )
        )
