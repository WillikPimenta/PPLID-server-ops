# -*- coding: utf-8 -*-
"""Views do portal — re-exporta pacote views/."""
from apps.falhas_criticas.views.analytics import *  # noqa: F403
from apps.falhas_criticas.views.exports import *  # noqa: F403
from apps.falhas_criticas.views.sync import *  # noqa: F403
from apps.falhas_criticas.views.base import ScopedAPIView  # noqa: F401
from apps.falhas_criticas.views.health import HealthView  # noqa: F401
from apps.falhas_criticas.views.portal import PortalView  # noqa: F401
