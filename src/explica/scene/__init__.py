"""Declarative scene engine: JSON scene sources → cairo frames → FFmpeg clips.

Every drawable has a stable `id`; animations target `id.property`. The JSON
file is the editable source of truth (A22); the rendered clip is a derived,
cached artifact keyed by the hash of the source plus render parameters.
"""

from .model import (  # noqa: F401
    OBJECT_TYPES,
    SceneError,
    animate,
    scene_hash,
    state_at,
    validate_scene,
)
from .render import SceneRenderer, render_scene  # noqa: F401
