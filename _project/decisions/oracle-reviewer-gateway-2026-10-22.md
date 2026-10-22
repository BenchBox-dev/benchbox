# Oracle reviewer credentials: gateway client token

Date: 2026-10-22
Status: Decided. The change is staged: it merges only after the owner's
gateway is live, and reverting it is the rollback.
Related: `.github/workflows/oracle-verdict.yml`,
`.github/oracle-omp-config.yml`, `_project/scripts/oracle_reviewers/cli.py`
(`reviewer-home`), `_project/scripts/oracle_reviewers/commands.py`,
`docs/internal/oracle-review-v2.md`,
`_project/decisions/oracle-review-v2-shadow-2026-10-05.md`.

## Context

The `attempt-N-omp` jobs ran the reviewer with raw provider credentials or the
credential broker's URL and token, plus the owner's OMP config as a secret.
The reviewer has no file confinement: it can read any file the runner user can,
through its tools, prompt `@` mentions, context-file imports and `cfg://`
reads. A pull request could therefore steer it toward those credentials, and a
provider key or the broker token is worth far more than the model calls the
reviewer needs.

## Decision

The reviewer process receives one credential, `OMP_GATEWAY_TOKEN`, from the
`oracle` environment secret `ORACLE_GATEWAY_TOKEN`, plus the base environment.
It is a client token for `omp auth-gateway` running on the owner's Mac in front
of the existing broker. The gateway limits the client to 11 named models and,
optionally, to pinned accounts, and the token is reachable only over the
tailnet.

- `commands.omp_env()` forwards `OMP_BASE_ENV` and `OMP_GATEWAY_TOKEN`
  only. `absence.CREDENTIAL_ENV` redacts `OMP_GATEWAY_TOKEN` and
  `TYPESAFE_API_KEY`.
- Provider keys, the broker URL and token, and the `OMP_AGENT_CONFIG` secret
  leave the attempt jobs.
- The model roles and fallback chains are committed in
  `.github/oracle-omp-config.yml`; role changes are now pull requests.
- Each attempt job runs `cli.py reviewer-home`, which writes that config and a
  `models.yml` pointing the four providers at the gateway URL
  (`ORACLE_GATEWAY_URL`) with `transport: pi-native` and
  `apiKey: OMP_GATEWAY_TOKEN`. It refuses an empty or non-`https://` URL.
- The overlay is unchanged here; its hardening keys arrive separately.

## Rejected: a `--trusted-extension` tool-call guard

A guard extension that confines `read`, `grep` and `glob` calls to the checkout
was considered and rejected:

- It covers tool arguments only. It misses prompt `@` mentions, context-file
  imports and the in-process `cfg://` reads.
- The credentials would still be in the process.
- It would copy omp's private path grammar, which changes between releases.
- Its `--help` check cannot pass on the pinned build.

Holding no real credential beats filtering access to one.

## Deferred

Process isolation (a container or separate user for the reviewer) would confine
files properly. It is not needed for the credential problem once the reviewer
holds only a scoped token, and it is a larger change. Revisit it if the token's
scope or the reviewer's reach grows.

## Retry settings

- **`retry.fallbackOn: usage-limit`.** The default, `any`, walks to GPT, Muse
  and Gemini on any error, including a 404 scope error, a revoked token or an
  outage. That hides mis-scoping and swaps the model family under a reviewer
  recorded as Claude. With `usage-limit`, only a usage limit walks the chain;
  other failures fail the attempt, and the absence logic handles them.
- **Chains keyed by primary model id.** On the pinned omp 18.12.1, role-keyed
  `fallbackChains` make every `--model @role` walk the `default` chain; a key
  containing `/` is matched by model and wins over roles. Keying by model id
  restores each role's own chain. A stub confirmed both behaviours. `@review`
  and `@plan` share `claude-opus-5-5` and so share one chain, as they did
  before. Tests check that every policy alias has a role, every role primary has
  exactly one chain key, no key is a role name, and the top-level keys are only
  `modelRoles` and `retry`.

## Accepted residual risks

- The reviewer can read its own client token (for example
  `/proc/self/environ` through `read`) and can put it in its output. The token
  is scoped to the listed models, tailnet-only and quick to rotate (replace the
  digest; effective within 10 seconds). The docs claim no file confinement.
- The overlay's fetch and context-file settings and the brief's neutralization
  of prompt `@` path mentions stay. They are defense in depth for integrity and
  spend, not the credential boundary.
- Not tested before this change: the tailnet HTTPS leg, the gateway's 255 s
  idle timeout on a long `@slow` or `@plan` thinking stretch, and Anthropic's
  real usage-limit signal through the gateway. The first hosted canary
  covers them.
- Do not enable omp `secrets.enabled`; it restores real values into URLs and
  output.

## Rollout and rollback

1. The owner sets up the gateway (token and digest, clients file, launchd
   agent, `tailscale serve`, ACL for `tag:oracle-ci`) and adds the
   `ORACLE_GATEWAY_TOKEN` and `ORACLE_GATEWAY_URL` secrets. The runbook is in
   `docs/internal/oracle-review-v2.md`.
2. Merge this change, then run a canary on a soundness pull request.
3. After the canary passes, delete the old secrets
   (`CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, `META_API_KEY`,
   `GEMINI_API_KEY`, `OMP_AUTH_BROKER_URL`, `OMP_AUTH_BROKER_TOKEN`,
   `OMP_AGENT_CONFIG`), remove the broker ACL entry for `tag:oracle-ci`, and
   rotate those credentials once, since an unconfined reviewer could reach
   them.

Rollback is reverting this change. It is safe until the old secrets are
deleted in step 3, so keep them until the canary has passed.
