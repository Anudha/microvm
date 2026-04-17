"""Registry — tracks concurrently-running instances.

Kept intentionally simple: a dict, a lock, and a couple of convenience
helpers. Anything more (persistence across restarts, RPC, etc.) would be
feature creep for a mini-OS core.
"""
from __future__ import annotations

import threading
from typing import Iterator

from microvm.instance import Instance


class Registry:
    def __init__(self) -> None:
        self._instances: dict[str, Instance] = {}
        self._lock = threading.Lock()

    def create(self, **kwargs) -> Instance:
        inst = Instance(**kwargs)
        with self._lock:
            if inst.name in self._instances:
                raise ValueError(f"instance already registered: {inst.name}")
            self._instances[inst.name] = inst
        return inst

    def add(self, inst: Instance) -> None:
        with self._lock:
            if inst.name in self._instances:
                raise ValueError(f"instance already registered: {inst.name}")
            self._instances[inst.name] = inst

    def get(self, name: str) -> Instance | None:
        with self._lock:
            return self._instances.get(name)

    def remove(self, name: str) -> None:
        with self._lock:
            self._instances.pop(name, None)

    def all(self) -> list[Instance]:
        with self._lock:
            return list(self._instances.values())

    def running(self) -> list[Instance]:
        return [i for i in self.all() if i.running]

    def stop_all(self) -> None:
        for inst in self.all():
            try:
                inst.stop()
            except Exception:
                pass

    def __iter__(self) -> Iterator[Instance]:
        return iter(self.all())

    def __len__(self) -> int:
        with self._lock:
            return len(self._instances)
