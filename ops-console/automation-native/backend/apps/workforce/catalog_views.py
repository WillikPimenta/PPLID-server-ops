"""API de configuração e leitura dos catálogos de Headcount."""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access import registry as R
from apps.access.permission_classes import HasPortalPermission, portal_perm
from apps.workforce.models import HeadcountCatalogItem
from apps.workforce.services.catalog import (
    CATALOG_META,
    active_options_by_catalog,
    resolve_catalog,
    seed_catalog_from_history,
    serialize_catalog_item,
)


def _catalog_not_found():
    return Response({"detail": "Catálogo não encontrado."}, status=status.HTTP_404_NOT_FOUND)


class HeadcountCatalogOptionsView(APIView):
    """Opções ativas para selects do formulário de ciclo (usuários HC)."""

    permission_classes = [IsAuthenticated, portal_perm(R.PLANEJAMENTO_HEADCOUNT_VIEW)]

    def get(self, request):
        seed_catalog_from_history()
        return Response({"options": active_options_by_catalog()})


class HeadcountCatalogConfigMetaView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.ADM_CATALOGOS

    def get(self, request):
        seed_catalog_from_history()
        results = []
        for catalog, meta in CATALOG_META.items():
            count = HeadcountCatalogItem.objects.filter(catalog=catalog).count()
            results.append(
                {
                    "catalog": catalog,
                    "slug": meta["slug"],
                    "title": meta["title"],
                    "description": meta["description"],
                    "count": count,
                }
            )
        return Response({"results": results})


class HeadcountCatalogConfigListView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.ADM_CATALOGOS

    def get(self, request, catalog: str):
        catalog_key = resolve_catalog(catalog)
        if not catalog_key:
            return _catalog_not_found()
        seed_catalog_from_history()
        qs = HeadcountCatalogItem.objects.filter(catalog=catalog_key).order_by(
            "sort_order", "value"
        )
        meta = CATALOG_META.get(catalog_key, {})
        return Response(
            {
                "catalog": catalog_key,
                "title": meta.get("title", catalog_key),
                "description": meta.get("description", ""),
                "results": [serialize_catalog_item(item) for item in qs],
            }
        )

    def post(self, request, catalog: str):
        catalog_key = resolve_catalog(catalog)
        if not catalog_key:
            return _catalog_not_found()

        payload = request.data if isinstance(request.data, dict) else {}
        value = (payload.get("value") or "").strip()
        if not value:
            return Response(
                {"errors": {"value": "Informe o valor."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if HeadcountCatalogItem.objects.filter(catalog=catalog_key, value=value).exists():
            return Response(
                {"errors": {"value": "Este valor já existe no catálogo."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        label = (payload.get("label") or value).strip()
        sort_order = payload.get("sort_order")
        if sort_order is None:
            last = (
                HeadcountCatalogItem.objects.filter(catalog=catalog_key)
                .order_by("-sort_order")
                .values_list("sort_order", flat=True)
                .first()
            )
            sort_order = (last or 0) + 1

        item = HeadcountCatalogItem.objects.create(
            catalog=catalog_key,
            value=value,
            label=label,
            sort_order=int(sort_order),
            active=bool(payload.get("active", True)),
        )
        return Response(serialize_catalog_item(item), status=status.HTTP_201_CREATED)


class HeadcountCatalogConfigDetailView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = R.ADM_CATALOGOS

    def patch(self, request, catalog: str, pk: int):
        catalog_key = resolve_catalog(catalog)
        if not catalog_key:
            return _catalog_not_found()

        item = HeadcountCatalogItem.objects.filter(pk=pk, catalog=catalog_key).first()
        if not item:
            return Response(status=status.HTTP_404_NOT_FOUND)

        payload = request.data if isinstance(request.data, dict) else {}
        errors: dict[str, str] = {}

        if "value" in payload:
            value = (payload.get("value") or "").strip()
            if not value:
                errors["value"] = "Informe o valor."
            elif (
                HeadcountCatalogItem.objects.filter(catalog=catalog_key, value=value)
                .exclude(pk=item.pk)
                .exists()
            ):
                errors["value"] = "Este valor já existe no catálogo."
            else:
                item.value = value

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        if "label" in payload:
            item.label = (payload.get("label") or item.value).strip()
        if "sort_order" in payload:
            item.sort_order = int(payload.get("sort_order") or 0)
        if "active" in payload:
            item.active = bool(payload.get("active"))

        item.save()
        return Response(serialize_catalog_item(item))
