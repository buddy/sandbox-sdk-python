"""Python SDK for managing Buddy sandboxes.

Everything the SDK exposes is importable from here: the entities, the errors,
the connection config, and every model the API returns or accepts.
"""

from __future__ import annotations

from buddy_sandbox.api.openapi.pydantic_gen import *  # noqa: F403
from buddy_sandbox.api.openapi.pydantic_gen import __all__ as _models
from buddy_sandbox.core.buddy_api_client import BuddyApiClient, SandboxScope
from buddy_sandbox.core.const import PACKAGE_VERSION
from buddy_sandbox.core.http_client import HttpClient, HttpError, HttpResponse, aclose
from buddy_sandbox.entity.command import Command
from buddy_sandbox.entity.filesystem import FileInfo, FileSystem
from buddy_sandbox.entity.sandbox import Sandbox
from buddy_sandbox.entity.snapshot import Snapshot
from buddy_sandbox.errors import ERROR_CODES, BuddySDKError, ErrorCode
from buddy_sandbox.utils.client import ConnectionConfig
from buddy_sandbox.utils.regions import API_URLS, REGIONS, Region

__version__ = PACKAGE_VERSION

__all__ = [
    "API_URLS",
    "ERROR_CODES",
    "REGIONS",
    "BuddyApiClient",
    "BuddySDKError",
    "Command",
    "ConnectionConfig",
    "ErrorCode",
    "FileInfo",
    "FileSystem",
    "HttpClient",
    "HttpError",
    "HttpResponse",
    "Region",
    "Sandbox",
    "SandboxScope",
    "Snapshot",
    "__version__",
    "aclose",
    *_models,
]
