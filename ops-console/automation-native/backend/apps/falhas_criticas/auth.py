# -*- coding: utf-8 -*-
"""Autenticação via sessão PPLID — sem tela de login própria do módulo."""
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin


class PPLIDSessionRequiredMixin(LoginRequiredMixin):
    """Redireciona para o login Vue do PPLID se a sessão Django não existir."""

    def get_login_url(self):
        base = settings.PPLID_FRONTEND_URL.rstrip("/")
        redirect_to = self.request.get_full_path()
        return f"{base}/?{urlencode({'redirect': redirect_to})}"
