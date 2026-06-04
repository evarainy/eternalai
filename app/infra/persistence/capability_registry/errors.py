"""Capability registry persistence errors."""

from __future__ import annotations


class CapabilityRegistryError(RuntimeError):
    """Base class for capability registry persistence failures."""


class DuplicateCapabilityError(CapabilityRegistryError):
    """Raised when a capability_id already exists."""


class CapabilityNotFoundError(CapabilityRegistryError):
    """Raised when a capability_id is not found."""
