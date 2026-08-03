"""Deprecated compatibility facade for the official composition root."""

from app.bootstrap import DependencyContainer, build_container

__all__ = ["DependencyContainer", "build_container"]
