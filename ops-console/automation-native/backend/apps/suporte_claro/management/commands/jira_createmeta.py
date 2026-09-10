# -*- coding: utf-8 -*-
"""Consulta createmeta Jira para mapear projetos/campos dos perfis Suporte Claro."""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.suporte_claro.services.jira_profiles import get_rest_profile
from apps.suporte_claro.services.jira_rest import (
    JiraCredentials,
    _session,
    jira_base_url,
    jira_base_configured,
    jira_timeout_seconds,
)
from django.conf import settings


class Command(BaseCommand):
    help = (
        "Lista issue types/campos do projeto do perfil (planejamento|processos) "
        "via /rest/api/2/issue/createmeta/{key}/issuetypes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--profile",
            default="planejamento",
            choices=["planejamento", "processos"],
            help="Perfil de formalização",
        )
        parser.add_argument(
            "--expand",
            action="store_true",
            help="Lista campos do issue type configurado no perfil",
        )
        parser.add_argument(
            "--token",
            default="",
            help="PAT Jira (ou use JIRA_API_TOKEN no .env só para este comando)",
        )
        parser.add_argument(
            "--username",
            default="",
            help="LAN ID (só para auth basic; em bearer o token basta)",
        )

    def handle(self, *args, **options):
        if not jira_base_configured():
            raise CommandError("Configure JIRA_BASE_URL no .env.")

        token = (options["token"] or str(getattr(settings, "JIRA_API_TOKEN", "") or "")).strip()
        username = (
            options["username"] or str(getattr(settings, "JIRA_USERNAME", "") or "cli")
        ).strip()
        if not token:
            raise CommandError("Informe --token ou JIRA_API_TOKEN no .env (só para este comando).")

        credentials = JiraCredentials(username=username, api_token=token)

        profile = get_rest_profile(options["profile"])
        base = jira_base_url()
        timeout = jira_timeout_seconds()
        url = f"{base}/rest/api/2/issue/createmeta/{profile.project_key}/issuetypes"

        self.stdout.write(f"GET {url}")
        try:
            with _session(credentials) as session:
                resp = session.get(url, timeout=timeout)
        except Exception as exc:
            raise CommandError(f"Falha de rede: {exc}") from exc

        if resp.status_code != 200:
            # Fallback: issueTypes no projeto
            with _session(credentials) as session:
                proj = session.get(f"{base}/rest/api/2/project/{profile.project_key}", timeout=timeout)
            if proj.status_code != 200:
                raise CommandError(f"HTTP {resp.status_code}: {(resp.text or '')[:800]}")
            data = proj.json()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Projeto {data.get('key')} — {data.get('name')} (via /project; createmeta={resp.status_code})"
                )
            )
            for it in data.get("issueTypes") or []:
                mark = " ← perfil" if it.get("name") == profile.issuetype_name else ""
                self.stdout.write(f"  Issue type: {it.get('name')} (id={it.get('id')}){mark}")
            return

        data = resp.json()
        self.stdout.write(
            self.style.SUCCESS(f"Projeto {profile.project_key} — {profile.label}")
        )
        type_id = None
        for it in data.get("values") or []:
            mark = " ← perfil" if it.get("name") == profile.issuetype_name else ""
            self.stdout.write(f"  Issue type: {it.get('name')} (id={it.get('id')}){mark}")
            if it.get("name") == profile.issuetype_name:
                type_id = it.get("id")

        if options["expand"] and type_id:
            fields_url = (
                f"{base}/rest/api/2/issue/createmeta/{profile.project_key}/issuetypes/{type_id}"
            )
            self.stdout.write(f"\nGET {fields_url}")
            with _session(credentials) as session:
                fields_resp = session.get(fields_url, timeout=timeout)
            if fields_resp.status_code != 200:
                raise CommandError(
                    f"HTTP {fields_resp.status_code}: {(fields_resp.text or '')[:800]}"
                )
            fdata = fields_resp.json()
            for field in fdata.get("values") or []:
                required = "required" if field.get("required") else "optional"
                field_id = field.get("fieldId") or field.get("key")
                name = field.get("name") or field_id
                self.stdout.write(f"    [{required}] {field_id} — {name}")

        self.stdout.write("")
        self.stdout.write("JSON resumido (primeiros 2000 chars):")
        dumped = json.dumps(data, ensure_ascii=False, indent=2)
        self.stdout.write(dumped[:2000])
