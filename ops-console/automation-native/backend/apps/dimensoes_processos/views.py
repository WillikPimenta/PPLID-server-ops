"""API de consulta/CRUD leve das dimensões de processos."""

from __future__ import annotations

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access.permission_classes import portal_perm
from apps.access.registry import PLANEJAMENTO_MEGAZORD_VIEW
from apps.dimensoes_processos.models import (
    DimCliente,
    DimEtapa,
    DimGrupoServico,
    DimNivelHierarquico,
    DimProduto,
    DimServico,
    DimWorkflow,
    MetaEtapa,
    ProjecaoSla,
)
from apps.escala_flex.models import HierarchicalLevel
from apps.prioridades_nh.models import NhPrioridadeFluxo

CATALOG_PERM = portal_perm(PLANEJAMENTO_MEGAZORD_VIEW)


class DimensaoCatalogPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


DIMENSION_META = [
    {
        "key": "produto",
        "slug": "produto",
        "title": "Produto",
        "description": "Tipos de produto (Documentoscopia, Compliance, etc.).",
    },
    {
        "key": "grupo_servico",
        "slug": "grupo-servico",
        "title": "Grupo de serviço",
        "description": "Agrupadores de serviço operacional.",
    },
    {
        "key": "servico",
        "slug": "servico",
        "title": "Serviço",
        "description": "Serviços e meta dia padrão.",
    },
    {
        "key": "clientes",
        "slug": "clientes",
        "title": "Clientes",
        "description": "Clientes / operações e classificação.",
    },
    {
        "key": "workflow",
        "slug": "workflow",
        "title": "Workflow",
        "description": "Workflows BRFlow e tipo de atendimento.",
    },
    {
        "key": "nivel_hierarquico",
        "slug": "nivel-hierarquico",
        "title": "Nível hierárquico",
        "description": "Dimensão de nível hierárquico (Projeção SLA / BRFlow).",
    },
    {
        "key": "nivel_hierarquico_atendimento",
        "slug": "nivel-hierarquico-atendimento",
        "title": "Nível hierárquico de atendimento",
        "description": "NHs operacionais usados na Escala Flex e no Monitoramento (Alteração de NH).",
    },
    {
        "key": "etapas",
        "slug": "etapas",
        "title": "Etapas",
        "description": "Catálogo de etapas de produção.",
    },
    {
        "key": "metas_etapa",
        "slug": "metas-etapa",
        "title": "Metas por etapa",
        "description": "Metas diárias com vigência por etapa/serviço.",
    },
    {
        "key": "projecao_sla",
        "slug": "projecao-sla",
        "title": "Projeção SLA",
        "description": "Regras de SLA por cliente, workflow, NH e janela.",
    },
    {
        "key": "prioridades_nh_fluxo",
        "slug": "prioridades-nh-fluxo",
        "title": "Prioridades NH × fluxo",
        "description": "Prioridade de fluxo por nível hierárquico (sync BrFlow — consulta).",
    },
]


class DimProdutoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DimProduto
        fields = ("id_produto", "tipo_produto")


class DimGrupoServicoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DimGrupoServico
        fields = ("id_grupo", "nome")


class DimServicoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DimServico
        fields = ("id_servico", "nome", "meta_dia", "grupo")


class DimClienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = DimCliente
        fields = ("id_cliente", "nome", "operations", "id_classificacao")


class DimWorkflowSerializer(serializers.ModelSerializer):
    class Meta:
        model = DimWorkflow
        fields = ("id_workflow", "nome", "ind_considerar", "produto", "tipo_atendimento")


class DimNivelHierarquicoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DimNivelHierarquico
        fields = ("id_nh", "nome", "ind_considerar")


class DimNivelHierarquicoAtendimentoSerializer(serializers.ModelSerializer):
    """CRUD Megazord sobre ef_hierarchical_level (fonte da combobox de NH)."""

    class Meta:
        model = HierarchicalLevel
        fields = ("id", "name", "active", "sharepoint_id")
        read_only_fields = ("id",)


class DimEtapaSerializer(serializers.ModelSerializer):
    class Meta:
        model = DimEtapa
        fields = ("id_etapa", "nome", "manual")
        extra_kwargs = {"id_etapa": {"required": False}}

    def create(self, validated_data):
        if validated_data.get("id_etapa") is None:
            from django.db.models import Max

            mx = DimEtapa.objects.aggregate(m=Max("id_etapa"))["m"] or 0
            validated_data["id_etapa"] = int(mx) + 1
        return super().create(validated_data)


class MetaEtapaSerializer(serializers.ModelSerializer):
    etapa_nome = serializers.CharField(source="etapa.nome", read_only=True)
    servico_nome = serializers.CharField(source="servico.nome", read_only=True, allow_null=True)

    class Meta:
        model = MetaEtapa
        fields = (
            "id",
            "data_inicio",
            "data_fim",
            "etapa",
            "etapa_nome",
            "meta_dia",
            "servico",
            "servico_nome",
        )


class ProjecaoSlaSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source="cliente.nome", read_only=True)
    workflow_nome = serializers.CharField(source="workflow.nome", read_only=True)
    nivel_hierarquico_nome = serializers.CharField(source="nivel_hierarquico.nome", read_only=True)

    class Meta:
        model = ProjecaoSla
        fields = (
            "id",
            "cliente",
            "cliente_nome",
            "data_inicio",
            "data_fim",
            "workflow",
            "workflow_nome",
            "nivel_hierarquico",
            "nivel_hierarquico_nome",
            "dias_semana",
            "hora_inicio",
            "hora_fim",
            "duracao_atendimento",
            "sla_segundos",
            "flag_ajuste_sla",
            "sla_ajuste",
            "volume",
        )


class NhPrioridadeFluxoSerializer(serializers.ModelSerializer):
    class Meta:
        model = NhPrioridadeFluxo
        fields = (
            "id",
            "prk_cliente",
            "nom_cliente",
            "prk_workflow",
            "nom_workflow",
            "prk_nivel_hierarquico",
            "nom_nivel_hierarquico",
            "prk_fluxo",
            "nom_fluxo",
            "prk_modulo",
            "nom_modulo",
            "num_prioridade_fluxo",
            "cod_analise",
            "hierarchical_level_id",
            "synced_at",
        )
        read_only_fields = fields


REGISTRY = {
    "produto": (DimProduto, DimProdutoSerializer, "id_produto"),
    "grupo-servico": (DimGrupoServico, DimGrupoServicoSerializer, "id_grupo"),
    "servico": (DimServico, DimServicoSerializer, "id_servico"),
    "clientes": (DimCliente, DimClienteSerializer, "id_cliente"),
    "workflow": (DimWorkflow, DimWorkflowSerializer, "id_workflow"),
    "nivel-hierarquico": (DimNivelHierarquico, DimNivelHierarquicoSerializer, "id_nh"),
    "nivel-hierarquico-atendimento": (
        HierarchicalLevel,
        DimNivelHierarquicoAtendimentoSerializer,
        "id",
    ),
    "etapas": (DimEtapa, DimEtapaSerializer, "id_etapa"),
    "metas-etapa": (MetaEtapa, MetaEtapaSerializer, "id"),
    "projecao-sla": (ProjecaoSla, ProjecaoSlaSerializer, "id"),
    "prioridades-nh-fluxo": (NhPrioridadeFluxo, NhPrioridadeFluxoSerializer, "id"),
}

SEARCH_FIELDS = {
    "produto": ("tipo_produto",),
    "grupo-servico": ("nome",),
    "servico": ("nome",),
    "clientes": ("nome",),
    "workflow": ("nome", "tipo_atendimento"),
    "nivel-hierarquico": ("nome",),
    "nivel-hierarquico-atendimento": ("name",),
    "etapas": ("nome",),
    "metas-etapa": ("etapa__nome", "servico__nome"),
    "projecao-sla": ("dias_semana", "cliente__nome", "workflow__nome", "nivel_hierarquico__nome"),
    "prioridades-nh-fluxo": (
        "nom_nivel_hierarquico",
        "nom_fluxo",
        "nom_workflow",
        "nom_cliente",
        "nom_modulo",
        "cod_analise",
    ),
}

# Filtros exatos por coluna (query ?col=a||b) — catálogos com facets.
COLUMN_FILTER_FIELDS = {
    "prioridades-nh-fluxo": (
        "nom_nivel_hierarquico",
        "num_prioridade_fluxo",
        "nom_fluxo",
        "nom_workflow",
        "nom_cliente",
        "nom_modulo",
        "synced_at",
    ),
}

FACET_FIELDS = {
    "prioridades-nh-fluxo": (
        "nom_nivel_hierarquico",
        "num_prioridade_fluxo",
        "nom_fluxo",
        "nom_workflow",
        "nom_cliente",
        "nom_modulo",
    ),
}

SELECT_RELATED = {
    "metas-etapa": ("etapa", "servico"),
    "projecao-sla": ("cliente", "workflow", "nivel_hierarquico"),
}


@api_view(["GET"])
@permission_classes([IsAuthenticated, CATALOG_PERM])
def dimensoes_meta_view(request):
    counts = {
        "produto": DimProduto.objects.count(),
        "grupo_servico": DimGrupoServico.objects.count(),
        "servico": DimServico.objects.count(),
        "clientes": DimCliente.objects.count(),
        "workflow": DimWorkflow.objects.count(),
        "nivel_hierarquico": DimNivelHierarquico.objects.count(),
        "nivel_hierarquico_atendimento": HierarchicalLevel.objects.count(),
        "etapas": DimEtapa.objects.count(),
        "metas_etapa": MetaEtapa.objects.count(),
        "projecao_sla": ProjecaoSla.objects.count(),
        "prioridades_nh_fluxo": NhPrioridadeFluxo.objects.count(),
    }
    items = [{**meta, "count": counts.get(meta["key"], 0)} for meta in DIMENSION_META]
    return Response({"ok": True, "items": items})


@api_view(["GET"])
@permission_classes([IsAuthenticated, CATALOG_PERM])
def etapas_proximo_id_view(request):
    """Próximo id_etapa = MAX(id_etapa)+1 (auto-incremento confiável)."""
    from django.db.models import Max

    mx = DimEtapa.objects.aggregate(m=Max("id_etapa"))["m"] or 0
    return Response({"ok": True, "next_id": int(mx) + 1})


@api_view(["GET"])
@permission_classes([IsAuthenticated, CATALOG_PERM])
def metas_etapa_etapas_faltantes_view(request):
    """Etapas da produtividade sem DimEtapa ou sem MetaEtapa vigente."""
    from apps.dimensoes_processos.services.etapas_faltantes import (
        DEFAULT_DAYS,
        list_etapas_faltantes,
    )

    raw_days = request.query_params.get("days")
    try:
        days = int(raw_days) if raw_days is not None else DEFAULT_DAYS
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    items = list_etapas_faltantes(days=days)
    return Response({"ok": True, "days": days, "count": len(items), "results": items})


class DimensaoCatalogViewSet(viewsets.ModelViewSet):
    """Listagem paginada + update pontual; create/delete restritos a dims pequenas."""

    permission_classes = [IsAuthenticated, CATALOG_PERM]
    pagination_class = DimensaoCatalogPagination
    http_method_names = ["get", "put", "patch", "post", "head", "options"]

    def get_slug(self) -> str:
        return str(self.kwargs.get("slug") or "")

    def get_model_bundle(self):
        slug = self.get_slug()
        if slug not in REGISTRY:
            return None
        return REGISTRY[slug]

    def get_queryset(self):
        bundle = self.get_model_bundle()
        if not bundle:
            return DimProduto.objects.none()
        model, _ser, pk_field = bundle
        qs = model.objects.all()
        related = SELECT_RELATED.get(self.get_slug())
        if related:
            qs = qs.select_related(*related)
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            from django.db.models import Q

            fields = SEARCH_FIELDS.get(self.get_slug(), ())
            q = Q()
            for field in fields:
                q |= Q(**{f"{field}__icontains": search})
            if search.isdigit():
                q |= Q(**{pk_field: int(search)})
            if q:
                qs = qs.filter(q)
        vigente = (self.request.query_params.get("vigente") or "").strip().lower()
        if vigente in {"1", "true", "yes"} and self.get_slug() in {"metas-etapa", "projecao-sla"}:
            qs = qs.filter(data_fim__isnull=True)
        qs = self._apply_column_filters(qs)
        qs = self._apply_ordering(qs)
        return qs

    def _parse_multi_values(self, raw: str) -> list[str]:
        text = (raw or "").strip()
        if not text:
            return []
        # Separador || evita conflito com vírgulas em nomes.
        parts = text.split("||") if "||" in text else text.split(",")
        return [p.strip() for p in parts if p.strip()]

    def _apply_column_filters(self, qs, *, exclude_field: str | None = None):
        slug = self.get_slug()
        allowed = COLUMN_FILTER_FIELDS.get(slug, ())
        if not allowed:
            return qs
        for field in allowed:
            if exclude_field and field == exclude_field:
                continue
            raw = self.request.query_params.get(f"col_{field}")
            if raw is None or str(raw).strip() == "":
                continue
            values = self._parse_multi_values(str(raw))
            if not values:
                qs = qs.none()
                break
            if field == "num_prioridade_fluxo":
                nums = []
                for v in values:
                    try:
                        nums.append(int(v))
                    except (TypeError, ValueError):
                        continue
                if not nums:
                    qs = qs.none()
                    break
                qs = qs.filter(num_prioridade_fluxo__in=nums)
            elif field == "synced_at":
                qs = qs.filter(synced_at__in=values)
            else:
                qs = qs.filter(**{f"{field}__in": values})
        return qs

    def _apply_ordering(self, qs):
        slug = self.get_slug()
        allowed = set(COLUMN_FILTER_FIELDS.get(slug, ())) | set(FACET_FIELDS.get(slug, ()))
        sort = (self.request.query_params.get("sort") or "").strip()
        if not sort:
            return qs
        desc = sort.startswith("-")
        field = sort[1:] if desc else sort
        if field not in allowed:
            return qs
        return qs.order_by(f"-{field}" if desc else field)

    def list(self, request, *args, **kwargs):
        if not self.get_model_bundle():
            return Response(
                {"ok": False, "message": "Catálogo inválido."},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Facets leves (distinct) — não devolve o dataset inteiro ao FE.
        if str(request.query_params.get("facets") or "").strip() in {"1", "true", "yes"}:
            return self._facets_response()
        return super().list(request, *args, **kwargs)

    def _facets_response(self):
        slug = self.get_slug()
        fields = FACET_FIELDS.get(slug, ())
        if not fields:
            return Response({"ok": True, "facets": {}})
        bundle = self.get_model_bundle()
        model, _ser, pk_field = bundle
        base_qs = model.objects.all()
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            from django.db.models import Q

            q = Q()
            for field in SEARCH_FIELDS.get(slug, ()):
                q |= Q(**{f"{field}__icontains": search})
            if search.isdigit():
                q |= Q(**{pk_field: int(search)})
            if q:
                base_qs = base_qs.filter(q)

        facets: dict[str, list] = {}
        for field in fields:
            # Encadeamento: aplica os outros filtros de coluna, exceto o da própria facet.
            qs = self._apply_column_filters(base_qs, exclude_field=field)
            values = list(qs.values_list(field, flat=True).distinct().order_by(field)[:5000])
            facets[field] = [
                str(v) if v is not None else ""
                for v in values
                if v is not None and str(v).strip() != ""
            ]
        return Response({"ok": True, "facets": facets})

    def get_serializer_class(self):
        bundle = self.get_model_bundle()
        if not bundle:
            return DimProdutoSerializer
        return bundle[1]

    def create(self, request, *args, **kwargs):
        slug = self.get_slug()
        # Fatos com vigência: só via /ciclos/ (regras sem gap)
        if slug in {"metas-etapa", "projecao-sla"}:
            return Response(
                {
                    "ok": False,
                    "message": "Criação via UI desabilitada para este catálogo. Use Novo ciclo / Alterar ciclo.",
                },
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        if slug == "prioridades-nh-fluxo":
            return Response(
                {
                    "ok": False,
                    "message": "Catálogo somente leitura. Atualizado pelo robô Prioridades por nível hierárquico.",
                },
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if self.get_slug() == "prioridades-nh-fluxo":
            return Response(
                {
                    "ok": False,
                    "message": "Catálogo somente leitura. Atualizado pelo robô Prioridades por nível hierárquico.",
                },
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if self.get_slug() == "prioridades-nh-fluxo":
            return Response(
                {
                    "ok": False,
                    "message": "Catálogo somente leitura. Atualizado pelo robô Prioridades por nível hierárquico.",
                },
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        return super().partial_update(request, *args, **kwargs)


def _validation_error_response(exc: Exception):
    from django.core.exceptions import ValidationError as DjangoValidationError

    if isinstance(exc, DjangoValidationError):
        messages = exc.messages if hasattr(exc, "messages") else [str(exc)]
        return Response(
            {"ok": False, "message": "; ".join(str(m) for m in messages)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    raise exc


def _parse_optional_int(value):
    if value is None or value == "":
        return None
    return int(value)


@api_view(["POST"])
@permission_classes([IsAuthenticated, CATALOG_PERM])
def metas_etapa_ciclos_view(request):
    """Criar / finalizar / rotacionar ciclos de MetaEtapa."""
    from apps.dimensoes_processos.services import vigencia as vig

    data = request.data if isinstance(request.data, dict) else {}
    acao = str(data.get("acao") or "").strip().lower()

    try:
        if acao == "criar":
            row = vig.criar_meta_ciclo(
                data_inicio=data.get("data_inicio"),
                etapa_id=int(data.get("etapa")),
                servico_id=_parse_optional_int(data.get("servico")),
                meta_dia=data.get("meta_dia"),
            )
            return Response(
                {"ok": True, "acao": acao, "item": MetaEtapaSerializer(row).data},
                status=status.HTTP_201_CREATED,
            )

        if acao == "finalizar":
            instance = MetaEtapa.objects.get(pk=data.get("id"))
            row = vig.finalizar_ciclo(instance, data.get("data_fim"))
            return Response({"ok": True, "acao": acao, "item": MetaEtapaSerializer(row).data})

        if acao == "rotacionar":
            instance = MetaEtapa.objects.select_related("etapa", "servico").get(pk=data.get("id"))
            antigo, novo = vig.rotacionar_meta_ciclo(
                instance,
                data_inicio_novo=data.get("data_inicio"),
                meta_dia=data.get("meta_dia", instance.meta_dia),
            )
            return Response(
                {
                    "ok": True,
                    "acao": acao,
                    "fechado": MetaEtapaSerializer(antigo).data,
                    "item": MetaEtapaSerializer(novo).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"ok": False, "message": "Ação inválida. Use criar, finalizar ou rotacionar."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except MetaEtapa.DoesNotExist:
        return Response({"ok": False, "message": "Ciclo não encontrado."}, status=status.HTTP_404_NOT_FOUND)
    except (TypeError, ValueError) as exc:
        return Response({"ok": False, "message": f"Payload inválido: {exc}"}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        return _validation_error_response(exc)


@api_view(["POST"])
@permission_classes([IsAuthenticated, CATALOG_PERM])
def projecao_sla_ciclos_view(request):
    """Criar / finalizar / rotacionar ciclos de ProjecaoSla."""
    from apps.dimensoes_processos.services import vigencia as vig

    data = request.data if isinstance(request.data, dict) else {}
    acao = str(data.get("acao") or "").strip().lower()

    try:
        if acao == "criar":
            row = vig.criar_sla_ciclo(
                data_inicio=data.get("data_inicio"),
                cliente_id=int(data.get("cliente")),
                workflow_id=int(data.get("workflow")),
                nivel_hierarquico_id=int(data.get("nivel_hierarquico")),
                dias_semana=data.get("dias_semana"),
                hora_inicio=data.get("hora_inicio") or None,
                hora_fim=data.get("hora_fim") or None,
                duracao_atendimento=data.get("duracao_atendimento"),
                sla_segundos=data.get("sla_segundos"),
                flag_ajuste_sla=data.get("flag_ajuste_sla"),
                sla_ajuste=data.get("sla_ajuste"),
                volume=data.get("volume"),
            )
            return Response(
                {"ok": True, "acao": acao, "item": ProjecaoSlaSerializer(row).data},
                status=status.HTTP_201_CREATED,
            )

        if acao == "finalizar":
            instance = ProjecaoSla.objects.get(pk=data.get("id"))
            row = vig.finalizar_ciclo(instance, data.get("data_fim"))
            return Response({"ok": True, "acao": acao, "item": ProjecaoSlaSerializer(row).data})

        if acao == "rotacionar":
            instance = ProjecaoSla.objects.select_related(
                "cliente", "workflow", "nivel_hierarquico"
            ).get(pk=data.get("id"))
            antigo, novo = vig.rotacionar_sla_ciclo(
                instance,
                data_inicio_novo=data.get("data_inicio"),
                hora_inicio=data.get("hora_inicio"),
                hora_fim=data.get("hora_fim"),
                duracao_atendimento=data.get("duracao_atendimento"),
                sla_segundos=data.get("sla_segundos"),
                flag_ajuste_sla=data.get("flag_ajuste_sla"),
                sla_ajuste=data.get("sla_ajuste"),
                volume=data.get("volume"),
            )
            return Response(
                {
                    "ok": True,
                    "acao": acao,
                    "fechado": ProjecaoSlaSerializer(antigo).data,
                    "item": ProjecaoSlaSerializer(novo).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"ok": False, "message": "Ação inválida. Use criar, finalizar ou rotacionar."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except ProjecaoSla.DoesNotExist:
        return Response({"ok": False, "message": "Ciclo não encontrado."}, status=status.HTTP_404_NOT_FOUND)
    except (TypeError, ValueError) as exc:
        return Response({"ok": False, "message": f"Payload inválido: {exc}"}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        return _validation_error_response(exc)
