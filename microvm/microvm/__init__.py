"""microvm — isolated Chromium instance manager.

Public API:
    from microvm import Instance, Registry, NetworkInterface, FilesystemInterface
"""
from microvm.instance import Instance
from microvm.registry import Registry
from microvm.interfaces.network import NetworkInterface
from microvm.interfaces.filesystem import FilesystemInterface

__version__ = "0.1.0"
__all__ = ["Instance", "Registry", "NetworkInterface", "FilesystemInterface"]
