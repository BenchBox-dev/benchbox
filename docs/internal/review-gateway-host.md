<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Review gateway host

The CI reviewers hold one credential: a client token for an
`omp auth-gateway` on the owner's Mac. This page rebuilds that Mac from
scratch. The design and its accepted risks are in
[`review-v2.md`](review-v2.md#credential-gateway) and
`_project/decisions/oracle-reviewer-gateway-2026-10-22.md`.

## What runs on the host

| Piece | Detail |
| --- | --- |
| omp build | The pinned `darwin-arm64` build from the `omp:` section of `.github/review.yml` on `origin/develop`, installed by `theseus install-omp` (the verified Theseus install of `origin/develop`) into `~/.cache/benchbox/omp/<version>/darwin-arm64/omp` after its SHA-256 and version check, the same cache local reviews use. The script runs both agents from that path. It never looks up `omp` on `PATH` and ignores an `OMP` environment variable, so the omp you run day to day is untouched. |
| Credential broker | launchd agent `dev.omp.auth-broker`: `<pinned omp> auth-broker serve --bind=127.0.0.1:8765 --trust-proxy-headers`. Provider logins live in `~/.omp/agent/agent.db`; its bearer token is `~/.omp/auth-broker.token`. Log: `~/.omp/logs/auth-broker.log`. |
| Gateway | launchd agent `dev.omp.auth-gateway`, which runs `~/.omp/review-gateway/run.sh`: `<pinned omp> auth-gateway serve --bind=127.0.0.1:4000 --clients=~/.omp/review-gateway/clients.json`, with `HOME=~/.omp/review-gateway/home` and the broker's URL and token. Log: `~/.omp/logs/auth-gateway.log`. |
| Client scope | `~/.omp/review-gateway/clients.json` (0600) admits `review-ci` and one client per [named client](#named-clients) token file. Each entry holds the SHA-256 of that client's token, every model `.github/review-omp-config.yml` on `origin/develop` lets a reviewer request from a granted provider, and every broker account logged in for those providers; `review-ci`'s token is `client.token`. A provider is granted when `.github/review-omp-config.yml` on `origin/develop` names it, it has at least one broker login, and it is not on the [owner denylist](#losing-or-removing-a-provider). Nothing in the script names a provider. `accounts` is also an allow-list: stored or environment API keys are never used for those providers. The gateway re-reads the file every 10 seconds. |
| Gateway admin token | The gateway creates `~/.omp/review-gateway/home/.omp/auth-gateway.token` (0600) on first start. It is not scoped by `clients.json`. Never copy it off the host. |
| Owner denylist | `~/.omp/review-gateway/disabled-providers` (0600, owned by you, host-only, never in the repository): one provider id per line, `#` starts a comment. `gateway` excludes each listed provider from `clients.json` and writes the list as `disabledProviders:` into `~/.omp/review-gateway/home/.omp/agent/config.yml`, which the script owns. |
| Health monitor | launchd agent `dev.omp.review-gateway-monitor`, installed by `monitor-install`: runs `review_gateway_host.sh monitor` every 15 minutes. State is `~/.omp/review-gateway/monitor.state`. Log: `~/.omp/logs/review-gateway-monitor.log`. |
| Tailnet | `tailscale serve --bg --https=4000 http://127.0.0.1:4000`, reachable only inside the tailnet. |
| GitHub | `oracle` environment secrets `ORACLE_GATEWAY_TOKEN` (the contents of `client.token`) and `ORACLE_GATEWAY_URL` (`https://<host>.<tailnet>.ts.net:4000`). |

`_project/scripts/review_gateway_host.sh` builds and checks all of this. A
rerun restarts an agent only when its plist or `run.sh` changed or its
process started before the pinned binary file was last written; a new
`clients.json` needs no restart. `gateway` fetches `origin/develop` and
generates `clients.json` from that export, never from the branch the host
checkout happens to be on. A provider without a login is excluded and
reported, never a reason to refuse: `gateway` refuses only when no provider is
granted.

## Account-level setup

These settings outlive any one machine. Redo them only if they are lost.

- **GitHub App and `oracle` environment:** see Setup in
  [`review-v2.md`](review-v2.md#setup).
- **Tailscale DNS settings:** MagicDNS and HTTPS Certificates are on, which
  `tailscale serve --https` needs. The first `tailscale serve` on a tailnet may
  print a link to enable Serve; open it.
- **Tailscale OAuth client** for the review job: Auth Keys write scope, tag
  `tag:oracle-ci`. Its client ID and secret are the `TS_OAUTH_CI_CLIENT_ID` and
  `TS_OAUTH_CI_SECRET` environment secrets of the `oracle` environment. The
  job holds no OIDC token. Revoke or rotate the client in the Tailscale admin
  console, Settings, OAuth clients.
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
   - No `omp` install is needed for the host: the script installs the pinned
     build itself. Every `broker`, `gateway` and `check` run prints
     `omp pin: <version> sha256=<hash>` for the build it resolved, and refuses a
     pinned build whose `omp auth-gateway` has no `--clients` (18.12.1, the
     current pin, has it; the gateway scopes every reviewer client with it).
2. Clone this repository. The host checkout may be on any branch: `gateway`
   fetches `origin/develop` and scopes every client to that branch's
   `.github/review-omp-config.yml`.
3. Start the broker: `_project/scripts/review_gateway_host.sh broker`.
4. Log in to the reviewer providers with the accounts the reviewer should spend.
   The providers are the ones `.github/review-omp-config.yml` on `origin/develop`
   names; the broker step prints a `<pinned omp> auth-broker login <provider>`
   line for each that has no login, and the gateway starts without any it
   lacks. Every account logged in for a provider is pinned, so log out of any
   the reviewer must not use (`<pinned omp> auth-broker logout <provider>`). The
   broker step prints the pinned binary's path.
5. Start the gateway: `_project/scripts/review_gateway_host.sh gateway`.
   - It creates `client.token` if there is none.
   - It generates `clients.json` through `theseus gateway-clients`, which
     lists each excluded provider (`excluded: <provider> (<reason>): <models>`)
     and prints the pinned accounts per provider, one line per client
     (`review-ci`, plus one for each [named client](#named-clients) token
     file). It refuses only when no provider is granted, and then it writes
     nothing and leaves the agents as they were.
   - It installs the agent, checks the token's scope, publishes the gateway
     with `tailscale serve` and prints the tailnet URL.
6. In the Tailscale admin console, disable key expiry for this Mac and point
   the policy's gateway host at its tailnet IPv4 (`tailscale ip -4`).
7. Store the secrets: `_project/scripts/review_gateway_host.sh secrets`.
8. Check everything: `_project/scripts/review_gateway_host.sh check`
   (`check --json` prints the same result as JSON). A healthy host prints:

   ```text
   omp pin: 18.12.1 sha256=<hash>
   broker: ok on 127.0.0.1:8765
   dev.omp.auth-broker: omp 18.12.1 sha256=<hash>
   dev.omp.auth-gateway: omp 18.12.1 sha256=<hash>
   provider: anthropic granted=true served=true
   provider: openai-codex granted=true served=true
   provider: google-antigravity granted=true served=true
   http://127.0.0.1:4000: healthz=200 models=11/11 usage=403
   https://<host>.<tailnet>.ts.net:4000: healthz=200 models=11/11 usage=403
   usage probes: ok
   secrets: ORACLE_GATEWAY_TOKEN and ORACLE_GATEWAY_URL are set
   ```

   `check` exits 0 when healthy, 3 when degraded and 1 when broken.
   - **Broken** (exit 1): the broker or the gateway is down or running an
     omp whose SHA-256 or version differs from the pin, the process launchd
     runs for either agent is not the one holding its port, a process
     started before its binary file was last written (a same-path re-pin
     that was not restarted), `run.sh` names another binary, the gateway
     lists a model `clients.json` does not grant, a client's models differ
     from `review-ci`'s, `/v1/usage` is not refused with a 403, or no
     provider is granted and served. The comparison is of the executable
     file the process was started from, as `ps` reports it.
   - **Degraded** (exit 3): the script prints one `degraded: <provider>:
     <reason>` line for each provider that is routed but not served:
     `no logged-in broker account`, `stale pin` (a login changed identity, so
     `clients.json` pins accounts the broker no longer has; run `gateway`),
     or `granted but not served`. A failed `omp auth-gateway check` usage
     probe is `degraded: usage probe: <first lines>`, and a gateway that
     lists fewer models than `clients.json` grants is also degraded.
   - **Notice** (exit 0): `notice: <provider> removed by owner; develop still
     names <provider>` for each provider on the denylist.

   With [named clients](#named-clients), each one gets its own line and must
   list the same models as `review-ci`.
9. Run one review through it:
   `gh workflow run review-verdict.yml --ref develop -f pr=<open soundness PR>`.
   It works when the pull request's `review-verdict` status becomes a
   decision (success or failure) instead of `pending: … absent`. The gateway
   log records only start-ups, not requests.

## Routine changes

- **Reviewer models changed** in `.github/review-omp-config.yml`: after the
  change merges, run `gateway` again so every client is allowed the new
  models.
- **Rotate the client token:** `rm ~/.omp/review-gateway/client.token`, then
  run `gateway` and `secrets`. The old token stops working within 10 seconds.
- **Rotate the broker token:** `<pinned omp> auth-broker token --regenerate >/dev/null`,
  then restart both agents, because `run.sh` reads the broker token when it
  starts:
  `launchctl kickstart -k gui/$(id -u)/dev.omp.auth-broker` and
  `launchctl kickstart -k gui/$(id -u)/dev.omp.auth-gateway`.
- **omp pin bumped** in `.github/review.yml`: after the change
  merges, run `broker` and `gateway` at a quiet moment. Each installs the new
  pinned build and restarts its agent because the plist and `run.sh` name the
  new path; a restart interrupts reviews in flight. Then run `check`.
- **Provider login changed:** log in again with `<pinned omp> auth-broker login
  <provider>`, then run `gateway` so the pinned accounts match. `check` reports
  `degraded: <provider>: stale pin` until you do.

Restarting the gateway fails any review attempt in flight; the next retry sweep
(on a push to `develop` or the hourly schedule) retries it.

## Losing or removing a provider

No single provider is required. `gateway` derives the providers from
`.github/review-omp-config.yml` on `origin/develop`, starts with the ones that
have a broker login, and says which it left out. Reviews keep running on the
providers still served: Theseus gives each reviewer the first served model of
its `fallbackChains` entry in that config, and the review status says
`degraded: <provider>`. A review fails closed, as pending, only when no
reviewer at or above the PR's size tier can run on a served model. Losing a
provider therefore needs neither a relaxation of the ruleset nor an emergency
merge; see [`merge-authority.md`](../agent/merge-authority.md#stand-in-approvals)
and [`emergency-merge.md`](../agent/emergency-merge.md).

- **A login expired or an account was cancelled:** nothing to do for reviews.
  The monitor reports it within 15 minutes. Log in again with
  `<pinned omp> auth-broker login <provider>` and run `gateway`, or remove the
  provider on purpose as below.
- **Remove a provider on purpose, no pull request needed:** add its id to
  `~/.omp/review-gateway/disabled-providers` (create the file with
  `umask 077`; the script refuses one that others can read or write), then run
  `gateway`. The provider leaves `clients.json` and the gateway's
  `disabledProviders`; `develop` may keep naming it. `check` and the monitor
  then print a notice, not an alert, until a pull request removes the provider
  from `.github/review-omp-config.yml`. Delete the line and run `gateway` to
  bring it back. The file is read only on the host and can only remove a
  provider: it can never add a model, and nothing in a pull request reads it.
- **Diversity:** `tests/unit/scripts/test_review_provider_diversity.py` keeps
  every role of that config spanning at least two providers, with a candidate
  off any one provider that still fits the inline overhead. The test files
  are gate paths, so weakening them gets the gate review.
- **Last provider gone:** `gateway` refuses (`no configured provider has a
  broker login`) and leaves `clients.json`, `run.sh` and the agents as they
  were; reviews end pending and the gate holds. The owner's per-PR stand-in
  is then the only way through.

### Monitor

`_project/scripts/review_gateway_host.sh monitor-install` installs the launchd
agent `dev.omp.review-gateway-monitor` (`StartInterval` 900, `RunAtLoad`). It
runs a 0700 snapshot of the script as it is on `origin/develop`
(`~/.omp/review-gateway/monitor-host.sh`), never the working tree, so a branch
checkout or an unreviewed edit cannot run unattended. Run `monitor-install`
again after a change to the script merges to refresh the snapshot. The launchd `PATH` is the one in effect at install, so run
it from a shell where `gh`, `jq` and `tailscale` resolve. Each run does the
read-only part of `check` (no `gh secret list`) and compares the result with
`~/.omp/review-gateway/monitor.state`. On a change, and only then, it:

1. shows a macOS notification (`osascript`);
2. creates, edits or closes the one open issue labelled
   `review-gateway-health` in the repository, through the owner's `gh` login
   (the label is created on first use);
3. records the new state.

If the issue cannot be filed, the state is not recorded, so the next run
notifies and tries again. Steady state, including a deliberate removal, is
silent. The scheduled `review-verdict` run in CI also goes red when the gateway
is unreachable or a provider in `develop`'s config is not served; that job needs
no token permission beyond the ones the caller already grants.

## Migrating to the review names

Theseus 0.2.0 renamed the gateway directory and the client names. Every step
below is owner-run on the live host; nothing in the repository does it. The
running gateway keeps serving from `~/.omp/oracle-gateway` until step 1 is done,
and `review_gateway_host.sh` now defaults to `~/.omp/review-gateway`, so run the
steps in order and do not run the script between the update and step 1. Until the
host is migrated, CI reviews end absent and local reviews refuse, both failing
closed.

1. Stop the agents, then move the directory: `launchctl bootout` the
   `dev.omp.auth-gateway` agent, `mv ~/.omp/oracle-gateway ~/.omp/review-gateway`.
2. Rename any per-client token files so they still derive client names:
   `local-client.token` and `cloud-client.token` keep their names; the client
   names derived from them become `review-local` and `review-cloud`, and
   `client.token` stays the base client, now `review-ci`.
3. Add `theseus-client.token` for the Theseus self-gate client `review-theseus`
   (`umask 077; openssl rand -base64 36 | tr -d '\n' >
   ~/.omp/review-gateway/theseus-client.token`).
4. Run `gateway` (it regenerates `clients.json` from `origin/develop` with
   `review-ci`, `review-local`, `review-cloud` and `review-theseus`, rewrites
   `run.sh` for the new directory and restarts the agent), then `check`.
5. The GitHub secrets keep their `ORACLE_*` names and values; the callers map
   them to the `REVIEW_*` names Theseus expects, so `secrets` needs no change.
6. Local review now reads only `~/.omp/review-gateway/local-client.token`, and
   host-token redaction scans only the new directory, so no copy of a token may
   stay in `~/.omp/oracle-gateway`. Remove it once `check` passes.

## After the script rename

The script was `_project/scripts/oracle_gateway_host.sh` before 2026-11-08; the
policy and omp config it reads from `origin/develop` moved to
`.github/review.yml` and `.github/review-omp-config.yml` in the same change.
The generated `~/.omp/review-gateway/run.sh` and the launchd labels
(`dev.omp.auth-broker`, `dev.omp.auth-gateway`, `dev.omp.review-gateway-monitor`)
did not change, so the running gateway keeps serving. The monitor agent runs the
old snapshot `~/.omp/review-gateway/monitor-host.sh`, which still looks for the
old file names on `origin/develop` and reports an error until it is refreshed.
After the rename merges, the owner-authorized agent runs from a checkout at the
merged `origin/develop`:

1. `_project/scripts/review_gateway_host.sh monitor-install` (replaces the
   snapshot with the renamed script from `origin/develop` and reloads the agent);
2. `_project/scripts/review_gateway_host.sh gateway` (regenerates `clients.json`
   and `run.sh` from the renamed policy files; it restarts the agent only if they
   changed), then `check`.

The `oracle` GitHub environment, the `ORACLE_*` secret names and `tag:oracle-ci`
stay as legacy infrastructure names (`benchbox-oracle-infra-names-20261108`).

## Named clients

Each caller besides CI has its own client, so its token can be rotated or
revoked without touching `review-ci`. A client is named `review-<name>` and its
token is `~/.omp/review-gateway/<name>-client.token`. `review-local`, which
local pre-PR reviews use, has `~/.omp/review-gateway/local-client.token`.
Every client gets the same models and pinned accounts as `review-ci` and its own
`tokenSha256`.

**No per-client limits.** `omp auth-gateway` accepts exactly three fields per
client in `clients.json`: `tokenSha256`, `models` and `accounts`; any other
field makes it reject the file. It has no per-client rate, concurrency or
quota setting, so every client draws on the same pinned broker accounts and a
leaked `review-local` or `review-cloud` token can spend the quota that
`review-ci` depends on. Limiting a client's `models` would only stop it doing
its job, so all clients stay identical and `check` fails if they differ. The
controls are the tailnet policy (only `tag:oracle-ci` and `tag:oracle-cloud`
nodes and the owner's devices reach the gateway), per-client tokens, and fast
revocation, which needs neither the broker nor a rebuild of the other clients.
Watch the providers' own usage pages for unexpected spend. No proxy is built
in front of the gateway to add limits.

- **Add a client:** create the token file with at least 32 random bytes, mode
  0600, and without printing it, then run `gateway`:

  ```sh
  (umask 077; openssl rand -base64 36 | tr -d '\n' > ~/.omp/review-gateway/local-client.token)
  _project/scripts/review_gateway_host.sh gateway
  ```

  The script writes one `clients.json` entry per token file, and the gateway
  loads it within 10 seconds without a restart. Hand the token to its caller
  through that caller's own secret store, never through a log or a chat.
- **Rotate a client:** replace its token file with a new one as above, run
  `gateway`, then update the caller's copy. The old token stops working within
  10 seconds. Other clients are unaffected.
- **Revoke a client:** `_project/scripts/review_gateway_host.sh revoke
  review-local`. It removes that client's entry from `clients.json` with one
  atomic file replacement and then deletes its token file. It does not call the
  broker, restart the gateway or rewrite any other entry, so it works with the
  broker down or a provider logged out and leaves `review-ci` untouched. The
  token stops working within 10 seconds; run `check` to confirm the remaining
  clients. `revoke` refuses `review-ci`: rotate that token instead.
- **Client names:** a client is `review-<name>` where `<name>` is lowercase
  letters and digits joined by single hyphens. `gateway`, `check` and `revoke`
  share that rule: a `*-client.token` file whose name does not fit makes
  `gateway` and `check` fail naming the file, so rename or remove it; none is
  skipped silently.
- **Check:** `check` prints each client's model count and fails if a client's
  `clients.json` models or its live `/v1/models` list differ from `review-ci`'s,
  if a client's `/v1/usage` is not refused with a 403, or if `clients.json` and
  the token files name different clients:

  ```text
  http://127.0.0.1:4000: healthz=200 models=11/11 usage=403
  http://127.0.0.1:4000: review-local models=11/11 usage=403
  ```

## Cloud builder client

Cloud builder sessions (see
[`cloud-builder.md`](../agent/cloud-builder.md#review-gateway-access)) use the
named client `review-cloud`, whose token file is
`~/.omp/review-gateway/cloud-client.token`. It already exists on the host; it
is created, rotated and revoked as any [named client](#named-clients), so
`review-ci` and `review-local` are never touched. The cloud environments hold
three values by name only: `REVIEW_CLOUD_GATEWAY_TOKEN` (the contents of
`cloud-client.token`), `REVIEW_GATEWAY_URL` and `TS_AUTHKEY`.

- **Rotate the cloud token:** replace `cloud-client.token` as shown under
  [named clients](#named-clients), run `gateway`, then set the new value as
  `REVIEW_CLOUD_GATEWAY_TOKEN` in each cloud environment. The old token stops
  working within 10 seconds; `review-ci` and `review-local` keep working.
- **Revoke cloud access:** `_project/scripts/review_gateway_host.sh revoke
  review-cloud`. To stop sessions from joining the tailnet at all, also
  revoke the `tag:oracle-cloud` auth key (below).
- **Rotate or revoke the Tailscale credential:** in the Tailscale admin
  console, Settings, Keys, revoke the auth key behind `TS_AUTHKEY`, generate a
  new reusable, ephemeral, pre-approved key tagged `tag:oracle-cloud`, and set
  it as `TS_AUTHKEY` in each cloud environment. Revoking it does not affect
  the OAuth client used by `review-ci`, nor the gateway tokens. Nodes
  already joined stay until their session ends; remove them in the Machines
  page to cut them off immediately.
- **Check:** `check` lists `review-cloud` with the other clients, with the
  same model count as `review-ci` and `/v1/usage` refused with a 403.

## Move to another Mac

Build the new host, then on the old Mac remove the gateway:

```sh
launchctl bootout gui/$(id -u)/dev.omp.auth-gateway
rm ~/Library/LaunchAgents/dev.omp.auth-gateway.plist
tailscale serve --https=4000 off
rm -rf ~/.omp/review-gateway
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
| `check` exits 3 | The host is degraded: read the `degraded:` lines. See [Losing or removing a provider](#losing-or-removing-a-provider). |
| `gateway` refuses with `disabled-providers must be a regular file you own` or `is mode …` | The denylist must be a regular file owned by you with no group or other access: `chmod 600 ~/.omp/review-gateway/disabled-providers`. |
| `check` reports fewer models than `clients.json` | The gateway has not loaded the file. Run `gateway`. |
| `tokenSha256` is not 64 hex characters | The clients file was written by hand. Run `gateway`, which generates it. |
| `launchctl bootstrap` fails with error 5 | The previous instance had not finished unloading. The script waits for it; when loading by hand, wait until `launchctl print` no longer finds the label. |
