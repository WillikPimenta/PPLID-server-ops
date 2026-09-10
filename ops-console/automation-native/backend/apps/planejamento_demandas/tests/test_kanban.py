from django.test import SimpleTestCase

from apps.planejamento_demandas.models import JiraDemanda
from apps.planejamento_demandas.services.kanban import build_kanban_columns, status_column_order


class KanbanColumnOrderTests(SimpleTestCase):
    def test_status_order_prefers_workflow_sequence(self):
        self.assertLess(status_column_order("Backlog")[0], status_column_order("Em andamento")[0])
        self.assertLess(status_column_order("Em andamento")[0], status_column_order("Concluído")[0])
        self.assertLess(status_column_order("Concluído")[0], status_column_order("Cancelado")[0])

    def test_build_kanban_columns_groups_and_sorts(self):
        class Row:
            def __init__(self, key, status):
                self.issue_key = key
                self.status_name = status
                self.status_kind = JiraDemanda.STATUS_KIND_OPEN
                self.updated_at_jira = None
                self.created_at_jira = None

        items = [Row("A", "Pausado"), Row("B", "Em andamento"), Row("C", "Em andamento")]
        columns = build_kanban_columns(items, per_column_limit=10)
        self.assertEqual([c["status_name"] for c in columns], ["Em andamento", "Pausado"])
        self.assertEqual(columns[0]["total"], 2)
        self.assertEqual([row.issue_key for row in columns[0]["items"]], ["C", "B"])
