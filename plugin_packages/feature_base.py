"""Included in each feature ZIP. Resolves the shared runtime at event time."""
from __future__ import annotations

import logging


class Feature:
    qqchat_part_api = 1
    turns = ()
    legacy = ()

    def _available_core(self):
        meta = self.context.get_registered_star("astrbot_plugin_qqchat_core")
        core = getattr(meta, "star_cls", None)
        if (getattr(meta, "activated", False) and getattr(core, "qqchat_core_api", None) == 1
                and core.runtime is not None and not core.bundled_active()):
            return core
        return None

    def host(self):
        core = self._available_core()
        if core is not None and core._attached_parts.get(self.slug) is self:
            return core
        return None

    async def initialize(self):
        self.__dict__.setdefault("_attached", None)
        core = self._available_core()
        if core:
            await core.attach(self)
        else:
            logging.getLogger(__name__).info("分件将在共享运行时加载后接入。")

    async def terminate(self):
        if self.__dict__.get("_attached") is not None:
            await self._attached.detach(self)

    def __getattr__(self, name):
        # A small number of native handlers call a sibling handler on this feature.
        # Common session state and ACL helpers are owned by the single runtime.
        if name.startswith("__") or name in {"context", "name", "_attached"}:
            raise AttributeError(name)
        core = self.host()
        if core is None:
            raise AttributeError(name)
        return getattr(core, name)
