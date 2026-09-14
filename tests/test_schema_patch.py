"""Guards the generated-model patches applied by ``scripts/cleanup_schemas.py``.

Every failure mode here is silent: an unpatched model either rejects a good
response, strips the scope from a create request - turning an environment
sandbox into a project one - or quietly drops the snapshot a restore was
supposed to start from.
"""

from __future__ import annotations

from pathlib import Path

from buddy_sandbox.api.openapi import pydantic_gen as generated
from buddy_sandbox.api.openapi.pydantic_gen import (
    AddSandboxBody,
    CreateFromSnapshotRequestWritable,
    SandboxResponse,
    ShortEnvironmentView,
)


def test_accepts_environment_ids_as_the_hashid_strings_the_api_really_returns() -> None:
    parsed = ShortEnvironmentView.model_validate({"id": "nZrnl40Y", "identifier": "staging"})

    assert parsed.id == "nZrnl40Y"


def test_keeps_scope_and_environment_in_the_create_sandbox_body() -> None:
    parsed = AddSandboxBody.model_validate(
        {
            "name": "New sandbox",
            "os": "ubuntu:24.04",
            "scope": "ENVIRONMENT",
            "environment": {"id": "nZrnl40Y"},
        }
    )

    # The client sends the parse result, not the input, so anything dropped here
    # never reaches the API.
    assert parsed.model_dump(exclude_unset=True) == {
        "name": "New sandbox",
        "os": "ubuntu:24.04",
        "scope": "ENVIRONMENT",
        "environment": {"id": "nZrnl40Y"},
    }


def test_matches_the_create_sandbox_body_union_in_declaration_order() -> None:
    parsed = AddSandboxBody.model_validate({"snapshot_id": "snap1", "name": "restored"})

    assert isinstance(parsed.root, CreateFromSnapshotRequestWritable)
    assert parsed.model_dump(exclude_unset=True) == {
        "snapshot_id": "snap1",
        "name": "restored",
    }


def test_defers_annotation_evaluation() -> None:
    # Models reference aliases the generator emits below their first use, which
    # only resolves on Python 3.14+ unless annotations are deferred.
    source = Path(generated.__file__).read_text(encoding="utf-8")

    assert "from __future__ import annotations" in source


def test_renders_enum_values_as_plain_strings() -> None:
    parsed = SandboxResponse.model_validate({"id": "s1", "status": "FAILED"})

    assert f"Status: {parsed.status}" == "Status: FAILED"


def test_re_exports_every_generated_model_from_the_package_root() -> None:
    # Installing the package has to be enough to reach any type the API returns.
    import buddy_sandbox

    missing = sorted(set(generated.__all__) - set(buddy_sandbox.__all__))

    assert not missing
