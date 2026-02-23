"""OnGarde Allowlist — E-009 False Positive / Allowlist Config.

Public API:
    AllowlistEntry  — single allowlist rule dataclass
    AllowlistLoader — loads and hot-reloads allowlist configuration
"""
from app.allowlist.loader import AllowlistEntry, AllowlistLoader

__all__ = ["AllowlistEntry", "AllowlistLoader"]
