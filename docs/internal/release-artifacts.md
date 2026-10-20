# Release artifact contract

Release admission consumes the exact wheel and sdist from a successful
push-triggered trunk run on `develop`. It never rebuilds or publishes packages.

## Producer

The `dist-artifact` job of `.github/workflows/trunk.yml` runs on each push to
`develop`. Its artifact name is
`dist-<full head SHA>-attempt-<positive run attempt>`.

The artifact contains exactly four regular files at its root:

- one wheel (`*.whl`);
- one sdist (`*.tar.gz`);
- `SHA256SUMS`, containing exactly their two SHA-256 hashes and basenames;
- `producer-receipt.json`.

The receipt has schema `1` and records `repository`, `repository_id`,
`head_repository_id`, `workflow_path`, `head_sha`, `run_id`, `run_attempt`,
`job_id`, `job_name`, `artifact_name`, `sha256sums_sha256`, and `files`.
`files` maps each distribution basename to its `size` and `sha256`.
The producer reads its actual job ID from the attempt-scoped GitHub jobs API.
It cannot record the artifact ID or ZIP digest before upload; admission obtains
those values from GitHub and binds them to this receipt.

This replaces the former three-file `dist-<SHA>` format. Older artifacts lack
attempt evidence and are refused. They cannot be upgraded by local metadata or
by choosing an earlier successful run.

## Admission

Run from an exact checkout of an annotated `v*` version tag:

```bash
uv run -- python scripts/release_artifact_consumer.py admit \
  --source . --tag v0.4.2 --output /tmp/benchbox-release-admission
```

The command fetches `develop`, checks tag/version agreement and ancestry, and
selects the latest exact-SHA trunk run. The run must belong to
`BenchBox-dev/BenchBox`, use `.github/workflows/trunk.yml`, and have completed
successfully on a `push` to `develop`. Its exact attempt must have one successful
`dist-artifact` job and one unexpired attempt-qualified artifact.

Admission verifies the API ZIP digest before reading any archive member. It
rejects paths, duplicate names, links, nonregular members, encryption, extra
files, and payloads larger than 256 MiB. It recomputes the distribution hashes,
requires the producer receipt to match the actual run and job attempt, and
checks package/version identity. The exact tagged source's distribution binary
verifier must exist and pass. Metadata is read again before an admission receipt
and verified files appear in the new output directory.

API reads and downloads explicitly use `github.com`, regardless of `GH_HOST`.
Downloads have a 60-second whole-stream deadline, including blocked reads.
Failed downloads reap their child process and remove only their owned partial
file. Verification reads regular files from a private snapshot of the exact
Git commit, not mutable checkout bytes or Git archive export filters. The
snapshot's total size is checked from tree metadata before any blob is written,
and each blob must match its declared size. Downloads use an unbuffered pipe so
closing a timed-out stream cannot wait on a reader's buffered lock. Download
processes own a separate POSIX session whose remaining pipe writers are
terminated during cleanup, including when the direct child has already exited.
The download fails closed on Windows, because tree termination after the direct
child exits needs a Job Object that has no native test yet.

Git children disable replacement objects, hooks, and the filesystem monitor,
ignore system and global configuration, and do not inherit `GIT_CONFIG_*`
variables. The verifier runs from the committed snapshot under `-I -S -B`: no
site packages, `.pth` files, `sitecustomize`, or Python environment. Because
`-I` removes the script directory from the import path, a bootstrap maps the
`benchbox` and `benchbox.utils` packages to the snapshot without running their
`__init__` files, so the committed `binary_manifest` is the one that executes.
Git and verifier children receive only an allowlisted environment (`PATH`,
locale, and temporary-directory variables). Credentials go to the `gh`
transport alone. The checkout is checked again before output publication.

The `fetch` that establishes the develop ancestor uses the fixed repository URL
over HTTPS only. It also forces the local version tag to the hosted tag object,
so a hosted tag that moved after checkout makes the checkout differ from the
tag and admission refuses it. Git is told to deny every non-HTTPS transport by
name and to clear credential and askpass helpers, so a repository-local
`core.sshCommand`, an `ext::` URL rewrite, a proxy command, or a different
`remote.origin` cannot run code or redirect it. `gh` receives the allowlisted
environment plus its own authentication, configuration location, and proxy and
CA settings, and nothing else; `GH_HOST` is ignored because the host is pinned.
Publication requires an output parent that is owned by the caller (or root) and
not group- or world-writable unless sticky, resolves that parent to its real
path, and refuses to publish if the staging directory's device and inode
changed.

Output publication uses an atomic no-replace directory rename: `renameat2` on
Linux, `renamex_np` on macOS, and `os.rename` on Windows. An existing or racing
destination, including an empty directory or dangling symlink, is never
replaced. Platforms without the required operation fail closed. Because the
download is POSIX-only, the Windows rename is not reachable through admission.

An admission receipt is byte and producer evidence. It is not a cryptographic
attestation or permission to release. Admission does not change hosted tag
rules, environments, or publishing workflows.

### Threat model

Admission defends against artifacts and metadata that do not belong to the
tagged commit: another run or attempt, a forged or edited receipt, a tampered
archive or distribution, a tag that moved before the fetch, and a verifier that
is not the committed one. It is not a defense against a hostile writer on the
same filesystem while it runs. Run it on an ephemeral, single-tenant runner,
from a checkout that no other principal can write.

Limits inside that boundary:

- The hosted tag is read once, at the fetch. A move during admission is not
  detected by admission itself; the publishing workflow re-checks the hosted
  tag object and peeled commit after approval, and the tag ruleset forbids
  moves by other collaborators. The ruleset bypass actor is the same account
  the agent runs as, so the workflow re-check is the control that binds that
  account.
- `release_flow.py` and `benchbox/utils/clock.py` are loaded into the
  credential-bearing process from the consumer's own directory, so they are
  trusted exactly as the consumer script is.
- The clean-checkout re-check detects the state at that instant, not an edit
  that is made and reverted.
- The output-parent check covers the immediate parent only. Ancestors that
  others can rename, and access-control lists, are not inspected. The staging
  identity check narrows the swap window; it does not bind file contents.
- Repository configuration that is not an executable transport helper, such as
  proxy settings, CA bundles, includes, and URL rewrites to another HTTPS host,
  is still honored.

## Publish-side gate

An admitted directory crosses a job boundary, an artifact upload and download,
before a publisher uses it. `scripts/release_admitted_dist.py` runs in the job
that publishes and confirms the directory is still the admitted one. It is
offline: it builds, downloads, and publishes nothing.
`.github/workflows/release-v2.yml` calls it in its install, UAT, publish, and
GitHub-release jobs.

The tag and commit come from the publishing run (`github.ref_name` and
`github.sha`), never from the directory. The directory must match its admission
receipt exactly: the five members `admit` writes, the producer receipt, the
receipt's digest of `SHA256SUMS`, and the name, size, hash, package, and version
of the wheel and sdist against the tag. Links, files that are not regular files,
files over the size limit, and receipt names that point outside the directory
are refused.

`--stage` writes only the wheel and sdist, from the bytes it verified, into a
new directory that must not exist, and the upload step publishes that directory
instead of the admitted one. The gate binds bytes to the admission receipt. It
does not repeat the GitHub provenance checks that admission made, and it keeps
the admission threat model above: it reads each file once and compares what it
read, but it does not defend against a hostile writer on the same filesystem.

## Release requirements

The publishing workflow still requires the live `v-tag-restricted` rule,
verifiable provenance attestation, all twelve Python 3.11–3.14 installation and
smoke cells across Linux/macOS/Windows, artifact-bound release UAT, the binary
manifest, `make release-check`, ruleset drift checks, and no open
release-blocking `t3:*` issue. Linux correctness evidence remains separate from
the deferred Darwin digest certification.

TestPyPI rehearsals must exercise refusal paths and verify installed hashes.
Production PyPI retains owner approval. The legacy `release.yml` stays
available through `workflow_dispatch` only, so a `v*` tag push never starts
two publishers.

## Publishing workflow

`.github/workflows/release-v2.yml` starts on a `v*` tag push, or by hand with a
`tag` input to re-run a tag whose first run failed before publishing. Tags are
PEP 440 with a `v` prefix. Pre-release tags (`v0.5.0rc1`) publish to TestPyPI
only; final tags (`v0.5.0`) publish to PyPI. Tags must be annotated
(`git tag -a`); the admission consumer refuses lightweight tags. The workflow
never rebuilds artifacts, never falls back to an older trunk run, and must not
be run after a trunk rerun on the tagged commit, because a rerun changes the
admitted hashes.

Gates, in order:

1. `admit` runs `make release-check`, the ruleset drift check without the
   readiness override variables, the `release-blocking` issue check
   (`scripts/release_blocker_check.py`; it passes when no maintainer has applied
   that label), then `scripts/release_artifact_consumer.py admit`. Admission
   also checks the `benchbox/_binaries` tree committed at the tag against the
   distributions.
2. The install matrix installs the staged wheel on Python 3.11 to 3.14 across
   Linux, macOS, and Windows.
3. Release UAT runs the `release-gate-*.yaml` configs against the staged wheel
   with the checkout package hidden, then runs `make uat-gate-check` with the
   digest of the staged distributions. The gate is red when any stage skipped a
   cell the config does not prune by design, lost cells to early-stop pruning,
   could not reach or start a required platform, attempted no cells, or recorded
   another digest.
4. The publish job waits for environment approval, re-checks the hosted tag
   object and peeled commit against the admission receipt
   (`scripts/release_tag_recheck.py`), stages only the verified files, attests
   their hashes, and publishes them to `test-pypi` or `pypi`.

Verify a published release with `gh attestation verify <file> --repo
BenchBox-dev/BenchBox --signer-workflow
BenchBox-dev/BenchBox/.github/workflows/release-v2.yml`.

PyPI and TestPyPI trusted publishers are bound to the workflow file name, so
the owner must register `release-v2.yml` publishers before the first
rehearsal. Recovery from an expired or superseded artifact is a fresh
qualifying trunk artifact plus full downstream revalidation, never an
older-success fallback.
