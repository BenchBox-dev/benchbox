<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Oracle gateway host

```{tags} contributor, operations, ci
```

The oracle's CI reviewers hold one credential: a client token for an
`omp auth-gateway` on the owner's Mac. This page rebuilds that Mac from
scratch. The design and its accepted risks are in
[`oracle-review-v2.md`](oracle-review-v2.md#credential-gateway) and
`_project/decisions/oracle-reviewer-gateway-2026-10-22.md`.

## What runs on the host

| Piece | Detail |
| --- | --- |
| Credential broker | launchd agent `dev.omp.auth-broker`: `omp auth-broker serve --bind=127.0.0.1:8765 --trust-proxy-headers`. Provider logins live in `~/.omp/agent/agent.db`; its bearer token is `~/.omp/auth-broker.token`. Log: `~/.omp/logs/auth-broker.log`. |
| Gateway | launchd agent `dev.omp.auth-gateway`, which runs `~/.omp/oracle-gateway/run.sh`: `omp auth-gateway serve --bind=127.0.0.1:4000 --clients=~/.omp/oracle-gateway/clients.json`, with `HOME=~/.omp/oracle-gateway/home` and the broker's URL and token. Log: `~/.omp/logs/auth-gateway.log`. |
| Client scope | `~/.omp/oracle-gateway/clients.json` (0600) admits `oracle-ci` and one client per [named client](#named-clients) token file. Each entry holds the SHA-256 of that client's token, every model `.github/oracle-omp-config.yml` lets a reviewer request, and every broker account logged in for those providers; `oracle-ci`'s token is `client.token`. `accounts` is also an allow-list: stored or environment API keys are never used for those providers. The gateway re-reads the file every 10 seconds. |
| Gateway admin token | The gateway creates `~/.omp/oracle-gateway/home/.omp/auth-gateway.token` (0600) on first start. It is not scoped by `clients.json`. Never copy it off the host. |
| Tailnet | `tailscale serve --bg --https=4000 http://127.0.0.1:4000`, reachable only inside the tailnet. |
| GitHub | `oracle` environment secrets `ORACLE_GATEWAY_TOKEN` (the contents of `client.token`) and `ORACLE_GATEWAY_URL` (`https://<host>.<tailnet>.ts.net:4000`). |

`_project/scripts/oracle_gateway_host.sh` builds and checks all of this. A
rerun restarts an agent only when its plist or `run.sh` changed; a new
`clients.json` needs no restart.

## Account-level setup

These settings outlive any one machine. Redo them only if they are lost.

- **GitHub App and `oracle` environment:** see Setup in
  [`oracle-review-v2.md`](oracle-review-v2.md#setup).
- **Tailscale DNS settings:** MagicDNS and HTTPS Certificates are on, which
  `tailscale serve --https` needs. The first `tailscale serve` on a tailnet may
  print a link to enable Serve; open it.
- **Tailscale OIDC credential** for the attempt jobs: `auth_keys` write scope,
  tag `tag:oracle-ci`, subject
  `repo:BenchBox-dev@319264189/benchbox@1135770337:environment:oracle` (the
  name-only subject is rejected with a 403). Its client ID and audience are the
  `TS_OAUTH_CLIENT_ID` and `TS_AUDIENCE` environment secrets.
- **Tailnet policy:** `tag:oracle-ci` has a `tagOwners` entry. The default
  allow-all rule's source is `autogroup:member`, so tagged nodes match nothing
  else. One rule lets `tag:oracle-ci` reach only the gateway host on
  `tcp:4000`. The relevant parts of the policy file, for example:

  ```json
  {
    "tagOwners": { "tag:oracle-ci": ["autogroup:admin"] },
    "hosts": { "oracle-gateway": "<tailnet IPv4 of the host>" },
    "acls": [
      { "action": "accept", "src": ["autogroup:member"], "dst": ["*:*"] },
      { "action": "accept", "src": ["tag:oracle-ci"], "dst": ["oracle-gateway:4000"] }
    ]
  }
  ```

## Build a host

The host must stay reachable: reviews end absent while it sleeps, is offline,
or has no one logged in.

- Use a Mac that stays on. Prevent sleep (System Settings, Energy, or
  `sudo pmset -a sleep 0`).
- launchd user agents run only in a logged-in desktop session. Turn on
  automatic login for the owner account (FileVault must be off), or log in
  again after every restart. Run the script from Terminal in that session,
  not over SSH.

Then:

1. Install the tools. `curl`, `openssl` and `plutil` ship with macOS.
   - With Homebrew: `brew install tailscale gh uv jq`.
   - Start the Tailscale daemon and join the tailnet:
     `sudo brew services start tailscale`, then `sudo tailscale up`.
   - Sign `gh` in as a repository admin: `gh auth login`.
   - Install `omp` 18.12.6 or later, which has `auth-gateway serve --clients`.
     Put it on `PATH`, or set `OMP=/path/to/omp` for every script run.
2. Clone this repository and stay on an up-to-date `develop`
   (`git switch develop && git pull`). The client is scoped to that
   checkout's `.github/oracle-omp-config.yml`; the script warns on any other
   branch.
3. Start the broker: `_project/scripts/oracle_gateway_host.sh broker`.
4. Log in to each reviewer provider with the account the oracle should spend:
   `omp auth-broker login anthropic`, then `openai-codex`, `muse-code` and
   `google-antigravity`. The broker step prints any that are missing. Every
   account logged in for a provider is pinned, so log out of any the oracle
   must not use (`omp auth-broker logout <provider>`).
5. Start the gateway: `_project/scripts/oracle_gateway_host.sh gateway`.
   - It creates `client.token` if there is none.
   - It generates `clients.json` through `cli.py gateway-clients`, which
     prints the model count and the pinned accounts per provider, one line
     per client (`oracle-ci`, plus one for each
     [named client](#named-clients) token file).
   - It installs the agent, checks the token's scope, publishes the gateway
     with `tailscale serve` and prints the tailnet URL.
6. In the Tailscale admin console, disable key expiry for this Mac and point
   the policy's gateway host at its tailnet IPv4 (`tailscale ip -4`).
7. Store the secrets: `_project/scripts/oracle_gateway_host.sh secrets`.
8. Check everything: `_project/scripts/oracle_gateway_host.sh check`. A
   healthy host prints:

   ```text
   broker: ok on 127.0.0.1:8765
   logins: anthropic openai-codex muse-code google-antigravity
   http://127.0.0.1:4000: healthz=200 models=11/11 usage=403
   https://<host>.<tailnet>.ts.net:4000: healthz=200 models=11/11 usage=403
   secrets: ORACLE_GATEWAY_TOKEN and ORACLE_GATEWAY_URL are set
   ```

   `models` must equal the model count in `clients.json`, and `/v1/usage`
   must be refused with a 403. With [named clients](#named-clients), each
   one gets its own line and must list the same models as `oracle-ci`.
9. Run one review through it:
   `gh workflow run oracle-verdict.yml --ref develop -f pr=<open soundness PR>`.
   It works when the pull request's `oracle-verdict` status becomes a
   decision (success or failure) instead of `pending: … absent`. The gateway
   log records only start-ups, not requests.

## Routine changes

- **Reviewer models changed** in `.github/oracle-omp-config.yml`: after the
  change merges, pull `develop` and run `gateway` again so the client is
  allowed the new models.
- **Rotate the client token:** `rm ~/.omp/oracle-gateway/client.token`, then
  run `gateway` and `secrets`. The old token stops working within 10 seconds.
- **Rotate the broker token:** `omp auth-broker token --regenerate >/dev/null`,
  then restart both agents, because `run.sh` reads the broker token when it
  starts:
  `launchctl kickstart -k gui/$(id -u)/dev.omp.auth-broker` and
  `launchctl kickstart -k gui/$(id -u)/dev.omp.auth-gateway`.
- **Provider login changed:** log in again with `omp auth-broker login
  <provider>`, then run `gateway` so the pinned accounts match.

Restarting the gateway fails any review attempt in flight; the next retry sweep
(on a push to `develop` or the hourly schedule) retries it.

## Named clients

Each caller besides CI has its own client, so its token can be rotated or
revoked without touching `oracle-ci`. A client is named `oracle-<name>` and its
token is `~/.omp/oracle-gateway/<name>-client.token`. `oracle-local`, which
local pre-PR reviews use, has `~/.omp/oracle-gateway/local-client.token`.
Every client gets the same models and pinned accounts as `oracle-ci` and its own
`tokenSha256`.

- **Add a client:** create the token file with at least 32 random bytes, mode
  0600, and without printing it, then run `gateway`:

  ```sh
  (umask 077; openssl rand -base64 36 | tr -d '\n' > ~/.omp/oracle-gateway/local-client.token)
  _project/scripts/oracle_gateway_host.sh gateway
  ```

  The script writes one `clients.json` entry per token file, and the gateway
  loads it within 10 seconds without a restart. Hand the token to its caller
  through that caller's own secret store, never through a log or a chat.
- **Rotate a client:** replace its token file with a new one as above, run
  `gateway`, then update the caller's copy. The old token stops working within
  10 seconds. Other clients are unaffected.
- **Revoke a client:** `rm ~/.omp/oracle-gateway/local-client.token`, then run
  `gateway`. The entry leaves `clients.json` and the token stops working within
  10 seconds.
- **Check:** `check` prints each client's model count and fails if a client's
  `clients.json` models or its live `/v1/models` list differ from `oracle-ci`'s,
  if a client's `/v1/usage` is not refused with a 403, or if `clients.json` and
  the token files name different clients:

  ```text
  http://127.0.0.1:4000: healthz=200 models=11/11 usage=403
  http://127.0.0.1:4000: oracle-local models=11/11 usage=403
  ```

## Move to another Mac

Build the new host, then on the old Mac remove the gateway:

```sh
launchctl bootout gui/$(id -u)/dev.omp.auth-gateway
rm ~/Library/LaunchAgents/dev.omp.auth-gateway.plist
tailscale serve --https=4000 off
rm -rf ~/.omp/oracle-gateway
```

If nothing else uses its broker, remove that too:

```sh
launchctl bootout gui/$(id -u)/dev.omp.auth-broker
rm ~/Library/LaunchAgents/dev.omp.auth-broker.plist
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Attempts end absent with connection errors | The Mac is asleep, offline or logged out, or an agent is down. Run `check`. |
| Attempts end absent with `exit code 1` after about two minutes while `check` passes | The tailnet policy does not let `tag:oracle-ci` reach the host on port 4000. Add the port to that rule. While an attempt job is on the tailnet, `tailscale debug netmap \| jq -r '.PacketFilterRules[].DstPorts[] \| select(.Ports.Last < 65535) \| "\(.IP):\(.Ports.First)"'` on the host lists the ports open to it; with no tagged node online it prints nothing. |
| Gateway answers 401 | `ORACLE_GATEWAY_TOKEN` does not match `client.token`. Run `secrets`. |
| Gateway answers 403 for a model | The model is not in `clients.json`. Pull `develop` and run `gateway`. |
| `check` reports fewer models than `clients.json` | The gateway has not loaded the file. Run `gateway`. |
| `tokenSha256` is not 64 hex characters | The clients file was written by hand. Run `gateway`, which generates it. |
| `launchctl bootstrap` fails with error 5 | The previous instance had not finished unloading. The script waits for it; when loading by hand, wait until `launchctl print` no longer finds the label. |
