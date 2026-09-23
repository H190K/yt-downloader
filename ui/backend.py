"""Single import point for everything the UI needs from the backend.

With ``H190K_FAKE_BACKEND=1`` the simulated backend in :mod:`ui._dev_fake` is used instead of
``core`` so the interface can be developed and demoed without the external tools.
"""
from __future__ import annotations

import os

FAKE_BACKEND: bool = os.environ.get("H190K_FAKE_BACKEND", "").strip() == "1"

if not FAKE_BACKEND:
    from core.config import load_config, save_config  # noqa: F401
    from core.deps import TOOLS, DependencyError, DependencyManager  # noqa: F401
    from core.engine import DownloadJob, EngineError, JobOptions, MediaInfo, fetch_info  # noqa: F401
    from core.paths import TOOLS_DIR, ensure_dirs, resource_path  # noqa: F401
else:  # same interface, simulated behavior
    from ui._dev_fake import (  # type: ignore[assignment]  # noqa: F401
        TOOLS,
        TOOLS_DIR,
        DependencyError,
        DependencyManager,
        DownloadJob,
        EngineError,
        JobOptions,
        MediaInfo,
        ensure_dirs,
        fetch_info,
        load_config,
        resource_path,
        save_config,
    )
