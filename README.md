# Buddy Sandbox SDK

Python SDK for managing Buddy sandboxes - isolated Ubuntu environments for running commands.

## Installation

```bash
pip install git+https://github.com/buddy/sandbox-sdk-python.git
```

Everything is importable from the package root - the entities, the errors, and
every model the API returns or accepts:

```python
from buddy_sandbox import Sandbox, SandboxResponse, SandboxAppView, ConnectionConfig
```

The package ships type information (`py.typed`), so those models are fully
typed in your editor.

## Usage

```python
import asyncio

from buddy_sandbox import Sandbox


async def main() -> None:
    identifier = "my-sandbox"

    try:
        sandbox = await Sandbox.get_by_identifier(identifier)
    except Exception:
        sandbox = await Sandbox.create(
            identifier=identifier,
            name="My Sandbox",
            os="ubuntu:24.04",
        )

    await sandbox.start()

    await sandbox.run_command(command="ping -c 5 buddy.works")

    await sandbox.stop()


asyncio.run(main())
```

Set the environment variables:

```bash
export BUDDY_TOKEN="your-api-token"
export BUDDY_WORKSPACE="your-workspace"
export BUDDY_PROJECT="your-project"          # Optional: see Scopes below
export BUDDY_ENVIRONMENT="your-environment"  # Optional: see Scopes below
export BUDDY_REGION="US"                     # Optional: US (default), EU, or AS
```

Only the token and the workspace are required - the project and the environment
decide where new sandboxes land.

The SDK is async throughout: every call is awaited, and log streaming is an
async iterator.

## Scopes

A sandbox lives in a project, in an environment, or directly in the workspace.
There is no scope parameter: the scope is derived from which of `project` and
`environment` you supply. Their values come from the env vars (`BUDDY_PROJECT`,
`BUDDY_ENVIRONMENT`), or from the `connection` object you can pass to any call -
see [Connection overrides](#connection-overrides).

| Project | Environment | Where the sandbox is created | Scope |
|---|---|---|---|
| `my-project` | – | in that project | `PROJECT` |
| – | `staging` | in the workspace-level environment `staging` | `ENVIRONMENT` |
| `my-project` | `staging` | in the environment `staging` **belonging to `my-project`** | `ENVIRONMENT` |
| – | – | in the workspace itself | `WORKSPACE` |

Giving both is not a conflict. Environments belong either to a project or to the
workspace, and the project decides which of the two `staging` means - nothing
else. The sandbox is scoped to the environment either way; it never ends up in
the project.

```python
from buddy_sandbox import ConnectionConfig, Sandbox

# in a project
await Sandbox.create(connection=ConnectionConfig(project="my-project"))

# in an environment of that project
await Sandbox.create(connection=ConnectionConfig(project="my-project", environment="staging"))

# in the workspace - nothing configured at all
await Sandbox.create()

# in the workspace despite a globally set BUDDY_PROJECT
await Sandbox.create(connection=ConnectionConfig(workspace="my-company"))
```

Resolving an environment identifier to its ID costs an extra request. Pass
`connection.environment_id` instead of `connection.environment` to skip it -
worth doing when you create or list sandboxes in a loop.

A `connection` that sets `workspace`, `project` or `environment` decides the
placement on its own: you get exactly those values and nothing from the env
vars. `ConnectionConfig(workspace="my-company")` therefore means that workspace
with no project, even if `BUDDY_PROJECT` is set. A `connection` that sets none
of the three - overriding only `token`, say - leaves the env vars in charge.

The lookup follows the same rule: with a project, only that project's
environments are searched; without one, only workspace-level ones. There is no
second attempt - a miss means the environment is not where you pointed, and
you get an error rather than a sandbox somewhere else.

`Sandbox.list()` and `Sandbox.list_snapshots()` return one scope at a time,
mirroring the API - listing across scopes means one call per scope.

Creating a sandbox outside a project - in an environment or in the workspace
itself - requires workspace admin rights.

## Waiting for readiness

`Sandbox.create()` blocks until the sandbox has finished setup and reached
`RUNNING`, so the instance it returns is ready to use. While waiting it polls
the API on a backoff starting at 100ms and growing to 500ms.

Pass `wait=False` to skip the wait and drive it yourself. The instance you get
back carries everything the create call returned - `id`, `identifier`, `url`,
`resources` - but connection details are not settled yet.

```python
sandbox = await Sandbox.create(identifier="my-sandbox", wait=False)

# ... do other work while the sandbox boots ...

await sandbox.wait_until_ready()  # setup_status: SUCCESS
await sandbox.wait_until_running()  # status: RUNNING
```

The waiters accept an explicit interval, which pins polling to that fixed value
instead of backing off:

```python
await sandbox.wait_until_running(500)  # check every 500ms
```

## Commands

`run_command()` blocks until the command finishes, streaming its output to
`sys.stdout` and `sys.stderr` as it arrives. Pass `stdout=None` / `stderr=None`
to silence it, or `detached=True` to get the `Command` back immediately.

```python
command = await sandbox.run_command(command="pytest -q", stdout=None, stderr=None)

print(command.data.exit_code)
print(await command.stdout())
```

Logs are an async iterator, so you can format them yourself:

```python
command = await sandbox.run_command(command="make build", detached=True, stdout=None, stderr=None)

async for entry in command.logs(follow=True):
    print(f"[{entry.type}] {entry.data}")

finished = await command.wait()
```

## Apps

Sandboxes can run multiple apps simultaneously. Each app is a long-running
process defined by a command string.

```python
sandbox = await Sandbox.create(
    identifier="my-sandbox",
    name="My Sandbox",
    os="ubuntu:24.04",
    first_boot_commands="apt-get update && apt-get install -y curl",
    apps=["node server.js", "python worker.py"],
    timeout=600,  # auto-stop after 10 minutes of inactivity
)

# List apps
for app in sandbox.data.apps or []:
    print(f'{app.id}: "{app.command}" -> {app.app_status}')

# Control individual apps
app_id = (sandbox.data.apps or [])[0].id

await sandbox.stop_app(app_id)
await sandbox.start_app(app_id)

logs = await sandbox.get_app_logs(app_id)
print(logs.logs)
```

## Fetching repositories and artifacts

Use `fetch` to clone repositories or download artifacts into the sandbox on
first boot. Each entry sets a `type` (`PROJECT_REPO`, `PUBLIC_REPO`, or
`ARTIFACT`) plus the fields relevant to it.

```python
await Sandbox.create(
    identifier="my-sandbox",
    fetch=[
        {
            "type": "PUBLIC_REPO",
            "repository": "https://github.com/octocat/Hello-World",
            "ref": "master",
            "path": "/workspace/hello",
            "build_command": "echo built",
        }
    ],
)
```

## Updating a sandbox

Use `sandbox.update()` to change configuration after creation - `timeout`,
`apps`, `endpoints`, `variables`, `tags`, and so on. The internal state is
updated with the API's response.

```python
sandbox = await Sandbox.get_by_identifier("my-sandbox")

await sandbox.update(timeout=1200, tags=["staging", "feature-x"])
```

Changing `first_boot_commands` leaves the sandbox in `setup_status: STALE` - the
new commands only apply on first boot, so the sandbox must be recreated for them
to take effect.

## File system

```python
await sandbox.fs.create_folder("/workspace/data")
await sandbox.fs.upload_file(b"hello", "/workspace/data/hello.txt")
await sandbox.fs.upload_file("./local-file.txt", "/workspace/data/copy.txt")

content = await sandbox.fs.download_file("/workspace/data/hello.txt")  # bytes
await sandbox.fs.download_file("/workspace/data", "./archive.tar.gz")  # a directory

for entry in await sandbox.fs.list_files("/workspace/data"):
    print(entry.name, entry.type, entry.size)

await sandbox.fs.delete_file("/workspace/data/hello.txt")
```

A `FileSystem` can also be built from a sandbox ID alone:

```python
from buddy_sandbox import FileSystem

fs = FileSystem.for_sandbox(sandbox_id)
```

## Snapshots

Take point-in-time snapshots of a sandbox and restore from them later. Each
snapshot is returned as a `Snapshot` instance with its own methods.

```python
sandbox = await Sandbox.get_by_identifier("my-sandbox")

# Create a snapshot. Returns immediately with status "CREATING".
snapshot = await sandbox.create_snapshot(name="before-deploy")

# Wait until the snapshot is "CREATED" before using it.
await snapshot.wait_until_ready()

# Create a new sandbox from the snapshot.
restored = await Sandbox.create_from_snapshot(snapshot.id, name="restored-sandbox")

# List all snapshots for this sandbox.
snapshots = await sandbox.list_snapshots()

# Delete a snapshot.
await snapshot.delete()
```

If you already have a snapshot ID from elsewhere, get the entity directly:

```python
snapshot = await Sandbox.get_snapshot_by_id(sandbox_id, snapshot_id)
```

`list_snapshots` and `delete_snapshot` mean one thing on the class and another
on an instance: on `Sandbox` they cover every snapshot in the current scope,
including orphans whose parent sandbox is gone; on a sandbox they cover that
sandbox's own.

```python
await Sandbox.list_snapshots()  # every snapshot in the scope
await sandbox.list_snapshots()  # this sandbox's snapshots

await Sandbox.delete_snapshot(id)  # no parent sandbox needed
await sandbox.delete_snapshot(id)  # this sandbox's snapshot
```

Convenience methods on `Sandbox` are available when you only have an ID:

```python
await sandbox.wait_for_snapshot_ready(snapshot_id)
```

## Regions

Configure the API region:

```bash
# Via environment variable (recommended)
export BUDDY_REGION="EU"
```

```python
# Or via connection config
sandbox = await Sandbox.create(
    identifier="my-sandbox",
    connection=ConnectionConfig(region="EU"),  # US, EU, or AS
)
```

## Connection overrides

Override the workspace and the credentials per call. Setting `workspace`,
`project` or `environment` here also decides where the sandbox goes - see
[Scopes](#scopes):

```python
await Sandbox.create(
    identifier="my-sandbox",
    name="My Sandbox",
    os="ubuntu:24.04",
    connection=ConnectionConfig(
        workspace="different-workspace",
        project="different-project",
        token="custom-token",
        region="EU",
    ),
)
```

## Errors

Every public method raises `BuddySDKError`, carrying a `code`, and for HTTP
failures a `status_code` and the API's own `details`.

```python
from buddy_sandbox import BuddySDKError, ERROR_CODES

try:
    await Sandbox.get_by_identifier("nope")
except BuddySDKError as error:
    print(error.code)  # HTTP_ERROR | VALIDATION_ERROR | GENERIC_ERROR
    print(error.status_code)  # 404
    print(error.details)  # the errors array the API returned
```

## Connections

Every call shares one connection pool, so there is nothing to open or close. A
program that keeps running after it is done with its sandboxes can release it:

```python
import buddy_sandbox

await buddy_sandbox.aclose()
```

## License

MIT
