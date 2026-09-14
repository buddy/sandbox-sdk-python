"""``Sandbox.create`` against the live API, one option at a time."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from buddy_sandbox import BuddySDKError, Sandbox
from tests.shared.naming import sandbox_identifier, sandbox_name

pytestmark = pytest.mark.integration


@pytest.fixture
async def created() -> AsyncIterator[list[Sandbox]]:
    """Collects everything a test creates and destroys it afterwards."""
    sandboxes: list[Sandbox] = []
    yield sandboxes
    await asyncio.gather(*(sandbox.destroy() for sandbox in sandboxes), return_exceptions=True)


class TestCreateOptions:
    async def test_creates_a_sandbox_with_default_options(self, created: list[Sandbox]) -> None:
        sandbox = await Sandbox.create(identifier=sandbox_identifier("default_opts"))
        created.append(sandbox)

        assert sandbox.data.id is not None
        assert sandbox.data.os == "ubuntu:24.04"

    async def test_creates_a_sandbox_with_a_custom_name(self, created: list[Sandbox]) -> None:
        name = sandbox_name("custom-name")

        sandbox = await Sandbox.create(name=name)
        created.append(sandbox)

        assert sandbox.data.name == name

    async def test_creates_a_sandbox_with_a_custom_identifier(self, created: list[Sandbox]) -> None:
        identifier = sandbox_identifier("custom_id")

        sandbox = await Sandbox.create(identifier=identifier)
        created.append(sandbox)

        assert sandbox.data.identifier == identifier

    async def test_creates_a_sandbox_on_ubuntu_22_04(self, created: list[Sandbox]) -> None:
        sandbox = await Sandbox.create(name=sandbox_name("ubuntu-22"), os="ubuntu:22.04")
        created.append(sandbox)

        assert sandbox.data.os == "ubuntu:22.04"

    async def test_creates_a_sandbox_with_a_custom_timeout(self, created: list[Sandbox]) -> None:
        sandbox = await Sandbox.create(identifier=sandbox_identifier("timeout"), timeout=600)
        created.append(sandbox)

        assert sandbox.data.timeout == 600

    async def test_creates_a_sandbox_that_fetches_a_public_repo(
        self, created: list[Sandbox]
    ) -> None:
        sandbox = await Sandbox.create(
            identifier=sandbox_identifier("fetch"),
            fetch=[
                {
                    "type": "PUBLIC_REPO",
                    "repository": "https://github.com/octocat/Hello-World",
                    "ref": "master",
                }
            ],
        )
        created.append(sandbox)

        assert any(
            item.repository == "https://github.com/octocat/Hello-World" and item.ref == "master"
            for item in sandbox.data.fetch or []
        )

    async def test_rejects_a_duplicate_identifier(self, created: list[Sandbox]) -> None:
        identifier = sandbox_identifier("duplicate_test")

        created.append(await Sandbox.create(identifier=identifier))

        with pytest.raises(BuddySDKError, match="'identifier' must be unique"):
            await Sandbox.create(identifier=identifier)
