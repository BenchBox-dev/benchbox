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
| Client scope | `~/.omp/oracle-gateway/clients.json` (0600) admits `oracle-ci` and one client per [named client](#named-clients) token file. Each entry holds the SHA-256 of that client's token, every model `.github/oracle-omp-config.yml` on `origin/develop` lets a reviewer request, and every broker account logged in for those providers; `oracle-ci`'s token is `client.token`. `accounts` is also an allow-list: stored or environment API keys are never used for those providers. The gateway re-reads the file every 10 seconds. |
| Gateway admin token | The gateway creates `~/.omp/oracle-gateway/home/.omp/auth-gateway.token` (0600) on first start. It is not scoped by `clients.json`. Never copy it off the host. |
| Tailnet | `tailscale serve --bg --https=4000 http://127.0.0.1:4000`, reachable only inside the tailnet. |
| GitHub | `oracle` environment secrets `ORACLE_GATEWAY_TOKEN` (the contents of `client.token`) and `ORACLE_GATEWAY_URL` (`https://<host>.<tailnet>.ts.net:4000`). |

`_project/scripts/oracle_gateway_host.sh` builds and checks all of this. A
rerun restarts an agent only when its plist or `run.sh` changed; a new
`clients.json` needs no restart. `gateway` fetches `origin/develop` and
generates `clients.json` from that export, never from the branch the host
checkout happens to be on.

## Account-level setup

These settings outlive any one machine. Redo them only if they are lost.

- **GitHub App and `oracle` environment:** see Setup in
  [`oracle-review-v2.md`](oracle-review-v2.md#setup).
- **Tailscale DNS settings:** MagicDNS and HTTPS Certificates are on, which
  `tailscale serve --https` needs. The first `tailscale serve` on a tailnet may
  print a link to enable Serve; open it.
- **Tailscale OIDC credential** for the review job: `auth_keys` write scope,
  tag `tag:oracle-ci`, subject
  `repo:BenchBox-dev@319264189/benchbox@1135770337:environment:oracle` (the
  name-only subject is rejected with a 403). Its client ID and audience are the
  `TS_OAUTH_CLIENT_ID` and `TS_AUDIENCE` environment secrets.
- **Tailscale auth key for cloud builders:** a reusable, ephemeral,
  pre-approved key that tags nodes `tag:oracle-cloud`. It is the `TS_AUTHKEY`
  variable of the cloud builder environments. Cloud sessions join in
  userspace-networking mode with in-memory state, so each session is one
  ephemeral node that disappears when the session ends.
- **Tailnet policy:** `tag:oracle-ci` and `tag:oracle-cloud` have `tagOwners`
  entries. The policy uses `grants`, not the older `acls` and host-alias
  form. The default allow-all grant's source is `autogroup:member`, so tagged
  nodes match nothing else. One grant per tag lets it reach only the gateway
  host on `tcp:4000`, and a `tests` block pins both results. The relevant
  parts of the policy file, for example:

  ```json
  {
    "tagOwners": {
      "tag:oracle-ci": ["autogroup:admin"],
      "tag:oracle-cloud": ["autogroup:admin"]
    },
    "grants": [
      { "src": ["autogroup:member"], "dst": ["*"], "ip": ["*"] },
      { "src": ["tag:oracle-ci"], "dst": ["<tailnet IPv4 of the host>"], "ip": ["tcp:4000"] },
      { "src": ["tag:oracle-cloud"], "dst": ["<tailnet IPv4 of the host>"], "ip": ["tcp:4000"] }
    ],
    "tests": [
      { "src": "tag:oracle-cloud", "accept": ["<tailnet IPv4 of the host>:4000"], "deny": ["<tailnet IPv4 of the host>:22"] },
      { "src": "tag:oracle-ci", "accept": ["<tailnet IPv4 of the host>:4000"], "deny": ["<tailnet IPv4 of the host>:22"] }
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
2. Clone this repository. The host checkout may be on any branch: `gateway`
   fetches `origin/develop` and scopes every client to that branch's
   `.github/oracle-omp-config.yml`.
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
  change merges, run `gateway` again so every client is allowed the new
  models.
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

**No per-client limits.** `omp auth-gateway` accepts exactly three fields per
client in `clients.json`: `tokenSha256`, `models` and `accounts`; any other
field makes it reject the file. It has no per-client rate, concurrency or
quota setting, so every client draws on the same pinned broker accounts and a
leaked `oracle-local` or `oracle-cloud` token can spend the quota that
`oracle-ci` depends on. Limiting a client's `models` would only stop it doing
its job, so all clients stay identical and `check` fails if they differ. The
controls are the tailnet policy (only `tag:oracle-ci` and `tag:oracle-cloud`
nodes and the owner's devices reach the gateway), per-client tokens, and fast
revocation, which needs neither the broker nor a rebuild of the other clients.
Watch the providers' own usage pages for unexpected spend. No proxy is built
in front of the gateway to add limits.

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
- **Revoke a client:** `_project/scripts/oracle_gateway_host.sh revoke
  oracle-local`. It removes that client's entry from `clients.json` with one
  atomic file replacement and then deletes its token file. It does not call the
  broker, restart the gateway or rewrite any other entry, so it works with the
  broker down or a provider logged out and leaves `oracle-ci` untouched. The
  token stops working within 10 seconds; run `check` to confirm the remaining
  clients. `revoke` refuses `oracle-ci`: rotate that token instead.
- **Client names:** a client is `oracle-<name>` where `<name>` is lowercase
  letters and digits joined by single hyphens. `gateway`, `check` and `revoke`
  share that rule: a `*-client.token` file whose name does not fit makes
  `gateway` and `check` fail naming the file, so rename or remove it; none is
  skipped silently.
- **Check:** `check` prints each client's model count and fails if a client's
  `clients.json` models or its live `/v1/models` list differ from `oracle-ci`'s,
  if a client's `/v1/usage` is not refused with a 403, or if `clients.json` and
  the token files name different clients:

  ```text
  http://127.0.0.1:4000: healthz=200 models=11/11 usage=403
  http://127.0.0.1:4000: oracle-local models=11/11 usage=403
  ```

## Cloud builder client

Cloud builder sessions (see
[`cloud-builder.md`](../agent/cloud-builder.md#oracle-gateway-access)) use the
named client `oracle-cloud`, whose token file is
`~/.omp/oracle-gateway/cloud-client.token`. It already exists on the host; it
is created, rotated and revoked as any [named client](#named-clients), so
`oracle-ci` and `oracle-local` are never touched. The cloud environments hold
three values by name only: `ORACLE_CLOUD_GATEWAY_TOKEN` (the contents of
`cloud-client.token`), `ORACLE_GATEWAY_URL` and `TS_AUTHKEY`.

- **Rotate the cloud token:** replace `cloud-client.token` as shown under
  [named clients](#named-clients), run `gateway`, then set the new value as
  `ORACLE_CLOUD_GATEWAY_TOKEN` in each cloud environment. The old token stops
  working within 10 seconds; `oracle-ci` and `oracle-local` keep working.
- **Revoke cloud access:** `_project/scripts/oracle_gateway_host.sh revoke
  oracle-cloud`. To stop sessions from joining the tailnet at all, also
  revoke the `tag:oracle-cloud` auth key (below).
- **Rotate or revoke the Tailscale credential:** in the Tailscale admin
  console, Settings, Keys, revoke the auth key behind `TS_AUTHKEY`, generate a
  new reusable, ephemeral, pre-approved key tagged `tag:oracle-cloud`, and set
  it as `TS_AUTHKEY` in each cloud environment. Revoking it does not affect
  the OIDC credential used by `oracle-ci`, nor the gateway tokens. Nodes
  already joined stay until their session ends; remove them in the Machines
  page to cut them off immediately.
- **Check:** `check` lists `oracle-cloud` with the other clients, with the
  same model count as `oracle-ci` and `/v1/usage` refused with a 403.

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
| Attempts end absent with `exit code 1` after about two minutes while `check` passes | The tailnet policy does not let `tag:oracle-ci` reach the host on port 4000. Add the port to that tag's grant. While the review job is on the tailnet, `tailscale debug netmap \| jq -r '.PacketFilterRules[].DstPorts[] \| select(.Ports.Last < 65535) \| "\(.IP):\(.Ports.First)"'` on the host lists the ports open to it; with no tagged node online it prints nothing. |
| Gateway answers 401 | `ORACLE_GATEWAY_TOKEN` does not match `client.token`. Run `secrets`. |
| Gateway answers 403 for a model | The model is not in `clients.json`. Run `gateway`. |
| `check` reports fewer models than `clients.json` | The gateway has not loaded the file. Run `gateway`. |
| `tokenSha256` is not 64 hex characters | The clients file was written by hand. Run `gateway`, which generates it. |
| `launchctl bootstrap` fails with error 5 | The previous instance had not finished unloading. The script waits for it; when loading by hand, wait until `launchctl print` no longer finds the label. |
