"""
Cache ya diski isiyoweza kuangusha tovuti.

Kila ukurasa unatumia cache (kitufe cha LIVE kwenye menyu, nakala za kurasa). Diski ikijaa
("No space left on device") au faili likiharibika, FileBasedCache ya Django inatupa kosa na kila
ukurasa unakuwa Server Error (500). Hapa kosa la diski linaandikwa kwenye log, na ukurasa
unatengenezwa bila cache: tovuti inakuwa nzito kidogo, lakini haianguki.
"""
import logging

from django.core.cache.backends.filebased import FileBasedCache

logger = logging.getLogger("news.cache")


class SafeFileBasedCache(FileBasedCache):
    def _safe(self, op, default, *args, **kwargs):
        try:
            return getattr(super(), op)(*args, **kwargs)
        except (OSError, EOFError, ValueError) as exc:     # diski imejaa / faili limeharibika
            logger.warning("cache %s failed: %s", op, exc)
            return default

    def get(self, key, default=None, version=None):
        return self._safe("get", default, key, default, version)

    def set(self, key, value, timeout=None, version=None):
        return self._safe("set", None, key, value, timeout, version)

    def add(self, key, value, timeout=None, version=None):
        return self._safe("add", False, key, value, timeout, version)

    def touch(self, key, timeout=None, version=None):
        return self._safe("touch", False, key, timeout, version)

    def delete(self, key, version=None):
        return self._safe("delete", False, key, version)

    def has_key(self, key, version=None):
        return self._safe("has_key", False, key, version)

    def clear(self):
        return self._safe("clear", None)
