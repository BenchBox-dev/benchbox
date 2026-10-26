(cli-auth)=
# `auth` - Hosted Submission Credentials

```{tags} reference, cli, results
```

Manage the token used by `benchbox submit --service`.

Tokens are stored in the operating system keyring through the Python
`keyring` package. They are not written to BenchBox config files. For
automation, `BENCHBOX_SUBMIT_TOKEN` takes precedence over
`BENCHBOX_SERVICE_TOKEN`, and both environment variables take precedence
over the keyring.

## Basic Syntax

```bash
uv run -- benchbox auth COMMAND [OPTIONS]
```

## Commands

| Command | Description |
|---------|-------------|
| `login` | Store a hosted results token in the OS keyring |
| `status` | Show whether a token is available for a service URL |
| `refresh` | Replace the stored keyring token |
| `logout` | Remove the stored keyring token |

## Examples

```bash
uv run -- benchbox auth login

uv run -- benchbox auth login --service https://staging.benchbox.dev/v1

uv run -- benchbox auth status

uv run -- benchbox auth refresh

uv run -- benchbox auth logout
```

In order, these commands: prompt for a token and store it securely, store a token for a staging service, check whether BenchBox can submit to the default service, replace a stored token, and remove a stored token.

Prefer the prompt or an environment variable for secrets. `benchbox auth login --token ...`
exists for controlled automation, but command-line token values can be captured
by shell history and process listings on some systems.

## Environment Tokens

For CI or ephemeral environments:

```bash
export BENCHBOX_SUBMIT_TOKEN=...
uv run -- benchbox submit --last --service
```

`BENCHBOX_SUBMIT_TOKEN` is the primary hosted submission variable.
`BENCHBOX_SERVICE_TOKEN` remains supported as a fallback for older automation.
Environment tokens are never saved by BenchBox. If an environment token is
active, `uv run -- benchbox auth refresh` asks you to update or unset that
environment variable instead of hiding a replacement keyring token behind it.

## Related

- [submit](submit.md) - Upload or package benchmark results
- [results](results.md#results) - Show hosted submission history
