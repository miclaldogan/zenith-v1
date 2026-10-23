"""Provider adapters. Only fake, deterministic adapters exist; no live model is called."""
from .base import (AdapterError, AgentCapabilities, AuthStatus, DirectorAdapter, ImplementerAdapter,
                   RunContext)
from .fake import FakeDirector, FakeImplementer

ADAPTERS = {"fake-director": FakeDirector, "fake-implementer": FakeImplementer}

__all__ = ["AdapterError", "AgentCapabilities", "AuthStatus", "DirectorAdapter", "ImplementerAdapter",
           "RunContext", "FakeDirector", "FakeImplementer", "ADAPTERS"]
