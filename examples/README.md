# Examples

Each example is a standalone script that talks to the real Buddy API.

## Setup

From the repository root:

```bash
uv sync
cp .env.example .env   # then fill in BUDDY_TOKEN, BUDDY_WORKSPACE, BUDDY_PROJECT
```

## Running

```bash
uv run python -m examples              # all of them, at once
uv run python -m examples ping list    # just these
uv run python -m examples --jobs 2     # throttled
uv run python -m examples.ping         # one, on its own
```

| Example | What it shows |
|---|---|
| `ping` | Streaming output, parallel commands, and fire-and-forget |
| `list` | Listing the sandboxes in the current scope |
| `lifecycle` | create → run → stop → start → restart → destroy |
| `exec` | A command's exit code and output in one call, and when to use `run_command` instead |
| `streaming` | Automatic streaming, and iterating the log stream yourself |
| `filesystem` | Listing, creating, uploading, downloading and deleting |
| `apps` | Long-running apps: starting, stopping and reading their logs |
| `error_handling` | Exit codes, stderr, raising on failure, expected failures |
| `update` | Changing timeout, tags and apps in place |
| `snapshots` | Snapshot, wait, restore, and verify the contents survived |
| `fetch` | Cloning a public repository on first boot |
| `scopes` | Project, environment and workspace placement side by side |

Each example runs in its own process, so one crashing cannot take the others
down, and their output is streamed as it arrives, labelled with the example it
came from.

A sandbox is polled hard while it boots, so the whole set at once can trip the
rate limit of an API that has not had its limits raised. `--jobs` throttles it.

`scopes` takes the environment from its arguments rather than from
`BUDDY_ENVIRONMENT`, which the SDK reads itself and which would otherwise move
every other example into that environment. Pass it the direct way to try that
part: `uv run python -m examples.scopes <environment> [project]`.
