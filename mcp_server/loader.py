"""Import a sibling module by file path, without putting its directory on sys.path.

The MCP server reuses three modules that live next to the services that own them:
the compiled OKF context (`ui/_okf_context.py`) and the two APIs' demo repositories.
Reaching them by appending their directories to `sys.path` works exactly once — the
second one shadows the first, and `ui/` in particular contains a `server.py` that
silently takes over this package's own module name. Loading by path keeps each one
addressable by where it lives rather than by who got imported first.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load(name: str, path: Path):
    """Load `path` as a module registered under `name` (cached on repeat calls)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
