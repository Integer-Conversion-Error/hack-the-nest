"""Apply a model's targeted edits to a copy of a typed JSON artifact."""

import json
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, StrictInt

_Artifact = TypeVar("_Artifact", bound=BaseModel)


class StructuredEdit(BaseModel):
    op: Literal["replace", "add", "remove"]
    path: list[str | StrictInt] = Field(min_length=1, description="Object keys and zero-based list indexes.")
    value_json: str | None = Field(default=None, description="A JSON-encoded replacement or insertion. Null for remove.")


def _get(container, key: str | int):
    if isinstance(container, dict) and isinstance(key, str) and key in container:
        return container[key]
    if isinstance(container, list) and isinstance(key, int) and 0 <= key < len(container):
        return container[key]
    raise ValueError(f"No artifact entry at {key!r}.")


def apply_edits(artifact: _Artifact, edits: list[StructuredEdit]) -> _Artifact:
    """Validate all edits before the caller publishes a revised artifact."""
    document = artifact.model_dump(mode="json")
    for edit in edits:
        parent = document
        for segment in edit.path[:-1]:
            parent = _get(parent, segment)
        key = edit.path[-1]
        if edit.op == "remove":
            _get(parent, key)
            del parent[key]
            continue

        if edit.value_json is None:
            raise ValueError(f"The {edit.op} at {edit.path!r} needs a JSON value.")
        value = json.loads(edit.value_json)
        if edit.op == "add":
            if not (isinstance(parent, list) and isinstance(key, int) and 0 <= key <= len(parent)):
                raise ValueError(f"The add at {edit.path!r} needs a list index from 0 through its length.")
            parent.insert(key, value)
        else:
            _get(parent, key)
            parent[key] = value
    return type(artifact).model_validate(document)
