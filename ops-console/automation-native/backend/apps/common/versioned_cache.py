# -*- coding: utf-8 -*-
"""Cache LocMem com invalidação por bump de versão."""
from __future__ import annotations

from django.conf import settings
from django.core.cache import cache


class VersionedCache:
    def __init__(
        self,
        prefix: str,
        *,
        ttl_setting: str,
        default_ttl: int = 120,
    ) -> None:
        self.prefix = prefix
        self._ttl_setting = ttl_setting
        self._default_ttl = default_ttl
        self.version_key = f"{prefix}:version"

    def cache_ttl(self) -> int:
        return int(getattr(settings, self._ttl_setting, self._default_ttl) or self._default_ttl)

    def get_cache_version(self) -> int:
        version = cache.get(self.version_key)
        if version is None:
            cache.add(self.version_key, 1)
            version = cache.get(self.version_key) or 1
        return int(version)

    def bump_cache_version(self) -> int:
        try:
            return int(cache.incr(self.version_key))
        except ValueError:
            cache.set(self.version_key, 1, timeout=None)
            return 1

    def key(self, *parts: str) -> str:
        version = self.get_cache_version()
        safe = ":".join(str(p or "") for p in parts)
        return f"{self.prefix}:v{version}:{safe}"

    def get(self, *parts: str):
        return cache.get(self.key(*parts))

    def set(self, value, *parts: str, timeout: int | None = None) -> None:
        if timeout is None:
            ttl = self.cache_ttl()
            if ttl <= 0:
                return
            timeout = ttl
        cache.set(self.key(*parts), value, timeout=timeout)

    def set_persistent(self, value, *parts: str) -> None:
        """Sem TTL curto — sobrevive até bump de versão."""
        cache.set(self.key(*parts), value, timeout=None)
