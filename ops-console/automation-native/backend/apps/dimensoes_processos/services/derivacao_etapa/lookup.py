# -*- coding: utf-8 -*-
"""Resolve nomes CSV → IDs Megazord (DimCliente, DimWorkflow, DimEtapa)."""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.dimensoes_processos.models import (
    DimCliente,
    DimEtapa,
    DimNomeAlias,
    DimWorkflow,
)
from apps.dimensoes_processos.services.derivacao_etapa.normalize import (
    normalize_csv_etapa_key,
    normalize_csv_name_key,
    workflow_name_candidates,
)
from apps.dimensoes_processos.services.meta_etapa_lookup import normalize_etapa_nome


class DerivacaoEtapaComparativoStatus:
    OK = "ok"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    ALIAS = "alias"


class MatchStrategy:
    EXACT = "exact"
    SUFFIX = "suffix"
    CONTEXTUAL = "contextual"
    ALIAS = "alias"


@dataclass(frozen=True)
class ResolveResult:
    id: int | None
    nome_megazord: str | None
    status: str
    match_strategy: str = ""

    @property
    def ok(self) -> bool:
        return self.id is not None and self.status in {
            DerivacaoEtapaComparativoStatus.OK,
            DerivacaoEtapaComparativoStatus.ALIAS,
        }


@dataclass
class MegazordLookup:
    cliente_by_name: dict[str, int] = field(default_factory=dict)
    cliente_names: dict[int, str] = field(default_factory=dict)
    workflow_by_name: dict[str, int] = field(default_factory=dict)
    workflow_names: dict[int, str] = field(default_factory=dict)
    etapa_by_name: dict[str, int] = field(default_factory=dict)
    etapa_names: dict[int, str] = field(default_factory=dict)
    etapas_by_id: dict[int, str] = field(default_factory=dict)
    etapa_nkeys: dict[int, str] = field(default_factory=dict)
    _suggest_cliente_keys: list[tuple[int, str]] = field(default_factory=list)
    _suggest_workflow_keys: list[tuple[int, str]] = field(default_factory=list)
    _suggest_etapa_keys: list[tuple[int, str]] = field(default_factory=list)
    alias_cliente: dict[str, int] = field(default_factory=dict)
    alias_workflow: dict[str, int] = field(default_factory=dict)
    alias_etapa: dict[str, int] = field(default_factory=dict)

    @classmethod
    def build(cls) -> MegazordLookup:
        lookup = cls()
        for cid, nome in DimCliente.objects.values_list("id_cliente", "nome"):
            key = normalize_csv_name_key(nome)
            if key and key not in lookup.cliente_by_name:
                lookup.cliente_by_name[key] = cid
            lookup.cliente_names[cid] = nome
            if key:
                lookup._suggest_cliente_keys.append((cid, key))

        for wid, nome in DimWorkflow.objects.values_list("id_workflow", "nome"):
            key = normalize_csv_name_key(nome)
            lookup.workflow_by_name[key] = wid
            lookup.workflow_names[wid] = nome
            if key:
                lookup._suggest_workflow_keys.append((wid, key))

        for etapa_id, nome in DimEtapa.objects.values_list("id_etapa", "nome"):
            key = normalize_etapa_nome(nome)
            if key and key not in lookup.etapa_by_name:
                lookup.etapa_by_name[key] = etapa_id
            lookup.etapa_names[etapa_id] = nome
            lookup.etapas_by_id[etapa_id] = nome
            lookup.etapa_nkeys[etapa_id] = key
            if key:
                lookup._suggest_etapa_keys.append((etapa_id, key))

        for alias in DimNomeAlias.objects.filter(ativo=True).select_related("cliente", "workflow", "etapa"):
            if alias.dimensao == DimNomeAlias.DIM_CLIENTE and alias.cliente_id:
                lookup.alias_cliente[alias.nome_origem_key] = alias.cliente_id
            elif alias.dimensao == DimNomeAlias.DIM_WORKFLOW and alias.workflow_id:
                lookup.alias_workflow[alias.nome_origem_key] = alias.workflow_id
            elif alias.dimensao == DimNomeAlias.DIM_ETAPA and alias.etapa_id:
                lookup.alias_etapa[alias.nome_origem_key] = alias.etapa_id

        return lookup

    def resolve_cliente(self, nome: str) -> ResolveResult:
        key = normalize_csv_name_key(nome)
        if not key:
            return ResolveResult(None, None, DerivacaoEtapaComparativoStatus.UNMATCHED)

        if key in self.alias_cliente:
            cid = self.alias_cliente[key]
            return ResolveResult(
                cid,
                self.cliente_names.get(cid, ""),
                DerivacaoEtapaComparativoStatus.ALIAS,
                MatchStrategy.ALIAS,
            )

        cid = self.cliente_by_name.get(key)
        if cid is not None:
            return ResolveResult(
                cid,
                self.cliente_names.get(cid, ""),
                DerivacaoEtapaComparativoStatus.OK,
                MatchStrategy.EXACT,
            )
        return ResolveResult(None, None, DerivacaoEtapaComparativoStatus.UNMATCHED)

    def resolve_workflow(self, nome: str) -> ResolveResult:
        key = normalize_csv_name_key(nome)
        if not key:
            return ResolveResult(None, None, DerivacaoEtapaComparativoStatus.UNMATCHED)

        if key in self.alias_workflow:
            wid = self.alias_workflow[key]
            return ResolveResult(
                wid,
                self.workflow_names.get(wid, ""),
                DerivacaoEtapaComparativoStatus.ALIAS,
                MatchStrategy.ALIAS,
            )

        for candidate in workflow_name_candidates(nome):
            ckey = normalize_csv_name_key(candidate)
            wid = self.workflow_by_name.get(ckey)
            if wid is not None:
                strategy = MatchStrategy.EXACT if ckey == key else MatchStrategy.SUFFIX
                return ResolveResult(
                    wid,
                    self.workflow_names.get(wid, ""),
                    DerivacaoEtapaComparativoStatus.OK,
                    strategy,
                )

        return ResolveResult(None, None, DerivacaoEtapaComparativoStatus.UNMATCHED)

    def resolve_etapa(self, nome: str, *, workflow_id: int | None = None) -> ResolveResult:
        key = normalize_csv_etapa_key(nome)
        if not key:
            return ResolveResult(None, None, DerivacaoEtapaComparativoStatus.UNMATCHED)

        if key in self.alias_etapa:
            eid = self.alias_etapa[key]
            return ResolveResult(
                eid,
                self.etapa_names.get(eid, ""),
                DerivacaoEtapaComparativoStatus.ALIAS,
                MatchStrategy.ALIAS,
            )

        eid = self.etapa_by_name.get(key)
        if eid is not None:
            return ResolveResult(
                eid,
                self.etapa_names.get(eid, ""),
                DerivacaoEtapaComparativoStatus.OK,
                MatchStrategy.EXACT,
            )

        if workflow_id is None:
            return ResolveResult(None, None, DerivacaoEtapaComparativoStatus.UNMATCHED)

        contextual = self._contextual_etapa_matches(key, workflow_id)
        if len(contextual) == 1:
            eid = contextual[0]
            return ResolveResult(
                eid,
                self.etapa_names.get(eid, ""),
                DerivacaoEtapaComparativoStatus.OK,
                MatchStrategy.CONTEXTUAL,
            )
        if len(contextual) > 1:
            return ResolveResult(None, None, DerivacaoEtapaComparativoStatus.AMBIGUOUS)

        return ResolveResult(None, None, DerivacaoEtapaComparativoStatus.UNMATCHED)

    def _contextual_etapa_matches(self, etapa_key: str, workflow_id: int | None) -> list[int]:
        matches: list[int] = []
        wf_name_key = normalize_csv_name_key(self.workflow_names.get(workflow_id or 0, ""))
        wf_tokens = [t for t in wf_name_key.split() if len(t) > 3] if workflow_id and wf_name_key else []

        for eid, nkey in self.etapa_nkeys.items():
            if nkey == etapa_key:
                matches.append(eid)
                continue
            if not (nkey.endswith(etapa_key) or etapa_key.endswith(nkey)):
                continue
            if workflow_id and wf_tokens:
                if any(t in nkey for t in wf_tokens):
                    matches.append(eid)
            elif not workflow_id:
                matches.append(eid)
        return list(dict.fromkeys(matches))

    def suggest_similar(
        self,
        dimensao: str,
        nome_origem: str,
        *,
        limit: int = 5,
    ) -> list[dict]:
        from difflib import SequenceMatcher, get_close_matches

        if dimensao == DimNomeAlias.DIM_CLIENTE:
            catalog = self._suggest_cliente_keys
            names = self.cliente_names
            query = normalize_csv_name_key(nome_origem)
        elif dimensao == DimNomeAlias.DIM_WORKFLOW:
            catalog = self._suggest_workflow_keys
            names = self.workflow_names
            query = normalize_csv_name_key(nome_origem)
        else:
            catalog = self._suggest_etapa_keys
            names = self.etapa_names
            query = normalize_csv_etapa_key(nome_origem)

        if not query or not catalog:
            return []

        key_to_id: dict[str, int] = {}
        choices: list[str] = []
        for cid, ckey in catalog:
            if ckey not in key_to_id:
                key_to_id[ckey] = cid
                choices.append(ckey)

        hits = get_close_matches(query, choices, n=limit * 3, cutoff=0.4)
        scored: list[tuple[int, str, float]] = []
        for ckey in hits:
            cid = key_to_id[ckey]
            score = SequenceMatcher(None, query, ckey).ratio() * 100
            scored.append((cid, names.get(cid, ""), score))

        scored.sort(key=lambda x: (-x[2], x[1]))
        seen: set[int] = set()
        out: list[dict] = []
        for cid, cname, score in scored:
            if cid in seen:
                continue
            seen.add(cid)
            out.append({"id": cid, "nome": cname, "score": round(score, 1)})
            if len(out) >= limit:
                break
        return out
