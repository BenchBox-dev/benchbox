.PHONY: agent-write-preflight
agent-write-preflight:
	@sh scripts/agent_write_preflight.sh

.PHONY: cloud-bootstrap
cloud-bootstrap:
	@sh scripts/cloud_bootstrap.sh

.PHONY: cloud-gateway
cloud-gateway:
	@sh scripts/cloud_gateway.sh "$(or $(ACTION),up)"

.PHONY: cloud-verify
cloud-verify:
	@sh scripts/cloud_verify.sh "$(or $(LEVEL),tests)"

.PHONY: pr-preflight
pr-preflight:
	@set -eu; \
	if [ -n "$${BENCHBOX_TEST_SESSION_OWNER:-}" ]; then \
		echo "pr-preflight: refusing to run inside a process that holds the host lock; run 'make pr-preflight' directly, never wrap the whole pipeline" >&2; \
		exit 2; \
	fi; \
	sh scripts/check_primary_clone_clean.sh report; \
	PREFLIGHT_BASE="$(or $(BASE),origin/develop)"; \
	if [ -z "$(BASE)" ]; then git fetch origin develop --quiet; fi; \
	PY_LIST=$$(mktemp); \
	TEST_LIST=$$(mktemp); \
	LINUX_LIST=$$(mktemp); \
	STAGE=$$(mktemp); \
	ALLOW=$$(mktemp); \
	trap 'RC=$$?; python3 scripts/preflight_receipt.py finalize --stage "$$STAGE" --exit-status "$$RC" --allowlist "$$ALLOW" || true; rm -f "$$PY_LIST" "$$TEST_LIST" "$$LINUX_LIST" "$$STAGE" "$$ALLOW"; exit "$$RC"' EXIT; \
	git show HEAD:.github/preflight-known-failures.json > "$$ALLOW" 2>/dev/null || rm -f "$$ALLOW"; \
	python3 scripts/preflight_receipt.py begin --stage "$$STAGE" --base-ref "$$PREFLIGHT_BASE"; \
	RECORD="python3 scripts/preflight_receipt.py step --stage $$STAGE --allowlist $$ALLOW"; \
	$$RECORD --name changelog-check -- $(MAKE) -s changelog-check; \
	uv run -- python _project/scripts/preflight_targets.py python --base-ref "$$PREFLIGHT_BASE" > "$$PY_LIST"; \
	uv run -- python _project/scripts/preflight_targets.py tests --base-ref "$$PREFLIGHT_BASE" > "$$TEST_LIST"; \
	if [ -s "$$PY_LIST" ]; then \
		echo "==> ruff (changed Python files)"; \
		$$RECORD --name ruff-check -- uv run -- ruff check --force-exclude $$(cat "$$PY_LIST"); \
		$$RECORD --name ruff-format -- uv run -- ruff format --check --force-exclude $$(cat "$$PY_LIST"); \
	else \
		echo "No Python files changed; skipping ruff."; \
	fi; \
	if [ ! -s "$$TEST_LIST" ]; then \
		echo "No test files map to the changed paths; CI runs the fast lane."; \
		exit 0; \
	fi; \
	echo "==> tests for changed files"; \
	STATUS=0; \
	$$RECORD --name pytest --ok-codes 0,5 -- env $$(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/-u \1/p') uv run -- python -m pytest -q -n 0 --tb=short --ff $$(cat "$$TEST_LIST") || STATUS=$$?; \
	if [ "$$STATUS" -eq 5 ]; then echo "Mapped tests are all deselected by the default markers."; STATUS=0; fi; \
	if [ "$$STATUS" -eq 0 ]; then \
		uv run -- python _project/scripts/preflight_targets.py linux --base-ref "$$PREFLIGHT_BASE" > "$$LINUX_LIST"; \
		if [ -s "$$LINUX_LIST" ]; then \
			echo "==> Linux run (change touches platform-branching code or platform skips)"; \
			$$RECORD --name linux-pytest -- bash scripts/linux_preflight_pytest.sh "$$LINUX_LIST" || STATUS=$$?; \
		fi; \
	fi; \
	exit "$$STATUS"

.PHONY: pr-preflight-fast-tests
pr-preflight-fast-tests:
	@set -eu; \
	if [ -n "$(PATH_DECISION)" ] || [ -n "$(PATH_LISTS)" ]; then \
		[ -n "$(PATH_DECISION)" ] && [ -f "$(PATH_DECISION)" ] || { echo "PATH_DECISION is required" >&2; exit 2; }; \
		[ -n "$(PATH_LISTS)" ] && [ -d "$(PATH_LISTS)" ] || { echo "PATH_LISTS is required" >&2; exit 2; }; \
		DECISION="$(PATH_DECISION)"; \
		LISTS="$(PATH_LISTS)"; \
	else \
		DECISION=$$(mktemp); \
		LISTS=$$(mktemp -d); \
		trap 'rm -f "$$DECISION"; rm -rf "$$LISTS"' EXIT; \
		git fetch origin develop --quiet; \
		uv run -- python scripts/path_filter_decision.py --base-ref origin/develop --json-out "$$DECISION" --lists-dir "$$LISTS" >/dev/null; \
	fi; \
	$(MAKE) -s pr-content-guard PATH_LISTS="$$LISTS"; \
	if uv run -- python scripts/path_filter_decision.py --json-in "$$DECISION" --check needs-code-ci >/dev/null; then \
		echo "==> fast tests (CI marker selection; coverage remains CI-only)"; \
		uv run -- python -m pytest -m "fast and not (slow or stress or resource_heavy or live_integration)" --tb=short --timeout=120 -q; \
	else \
		echo "No code changes detected; skipping fast tests."; \
	fi

pr-landing-start:
	uv run -- python scripts/pr_landing.py --repo "$(or $(REPO),BenchBox-dev/BenchBox)" \
		--worktree . --branch "$(or $(BRANCH),$(shell git branch --show-current))" \
		$(if $(WORKTREE_ID),--worktree-id "$(WORKTREE_ID)",) start

pr-landing-withdraw:
	@[ -n "$(PR)" ] || { echo "PR is required" >&2; exit 2; }; \
	uv run -- python scripts/pr_landing.py --repo "$(or $(REPO),BenchBox-dev/BenchBox)" \
		--worktree . --branch "$(or $(BRANCH),$(shell git branch --show-current))" \
		$(if $(WORKTREE_ID),--worktree-id "$(WORKTREE_ID)",) \
		$(if $(PR_NODE_ID),--pr-node-id "$(PR_NODE_ID)",) withdraw --pr "$(PR)" \
		$(if $(HEAD),--expected-head "$(HEAD)",)

pr-landing-ready:
	@set -eu; \
	[ -n "$(PR)" ] || { echo "PR is required" >&2; exit 2; }; \
	[ -n "$(HEAD)" ] || { echo "HEAD is required" >&2; exit 2; }; \
	[ -n "$(EVIDENCE)" ] || { echo "EVIDENCE is required" >&2; exit 2; }; \
	$(if $(ARM),uv run -- python scripts/trunk_revert.py arm-gate --pr "$(PR)" --repo "$(or $(REPO),BenchBox-dev/BenchBox)"; ,)\
	uv run -- python scripts/pr_landing.py --repo "$(or $(REPO),BenchBox-dev/BenchBox)" \
		--worktree . --branch "$(or $(BRANCH),$(shell git branch --show-current))" \
		$(if $(WORKTREE_ID),--worktree-id "$(WORKTREE_ID)",) \
		$(if $(PR_NODE_ID),--pr-node-id "$(PR_NODE_ID)",) ready --pr "$(PR)" \
		--expected-head "$(HEAD)" --evidence-json "$(EVIDENCE)" \
		$(if $(ARM),--arm,) $(if $(BATCH),--require-batch,)

pr-followup-resume:
	@[ -n "$(KEY)" ] || { echo "KEY is required" >&2; exit 2; }; \
	uv run -- python scripts/pr_landing.py --worktree . followup-resume --key "$(KEY)"

.PHONY: pr-content-guard
pr-content-guard:
	@set -eu; \
	[ -n "$(PATH_LISTS)" ] || { echo "PATH_LISTS is required"; exit 2; }; \
	EXISTING=$$(mktemp); \
	trap 'rm -f "$$EXISTING"' EXIT; \
	$(MAKE) artifact-hygiene; \
	if [ -s "$(PATH_LISTS)/yaml.txt" ]; then \
		: > "$$EXISTING"; \
		while IFS= read -r path; do \
			if [ -e "$$path" ]; then printf '%s\n' "$$path" >> "$$EXISTING"; else echo "Skipping deleted YAML path: $$path"; fi; \
		done < "$(PATH_LISTS)/yaml.txt"; \
		if [ -s "$$EXISTING" ]; then uv run -- pre-commit run check-yaml --files $$(cat "$$EXISTING"); else echo "No existing YAML content paths to lint."; fi; \
	else \
		echo "No YAML content paths changed."; \
	fi; \
	if [ -s "$(PATH_LISTS)/markdown.txt" ]; then \
		: > "$$EXISTING"; \
		while IFS= read -r path; do \
			if [ -e "$$path" ]; then printf '%s\n' "$$path" >> "$$EXISTING"; else echo "Skipping deleted markdown path: $$path"; fi; \
		done < "$(PATH_LISTS)/markdown.txt"; \
		if [ -s "$$EXISTING" ]; then uv run -- pre-commit run markdownlint --files $$(cat "$$EXISTING"); else echo "No existing markdown content paths to lint."; fi; \
	else \
		echo "No markdown content paths changed."; \
	fi; \
	if [ -s "$(PATH_LISTS)/docs.txt" ]; then \
		$(MAKE) docs-validate; \
	else \
		echo "No docs paths changed."; \
	fi

.PHONY: pr-open
pr-open: override TITLE := $(value TITLE)
pr-open: export PR_OPEN_TITLE := $(TITLE)
pr-open: override PR_BODY_FILE := $(value PR_BODY_FILE)
pr-open: export PR_OPEN_BODY_FILE := $(PR_BODY_FILE)
pr-open: override PARENT := $(value PARENT)
pr-open: export PR_OPEN_PARENT := $(PARENT)
pr-open: override REBUTTALS_FILE := $(value REBUTTALS_FILE)
pr-open: export PR_OPEN_REBUTTALS := $(REBUTTALS_FILE)
pr-open: override ADJUDICATE := $(value ADJUDICATE)
pr-open: export PR_OPEN_ADJUDICATE := $(ADJUDICATE)
pr-open:
	@set -eu; \
	ROLE=$$(git config --worktree --get benchbox.batch.role 2>/dev/null || true); \
	if [ "$$ROLE" = member ]; then \
		echo "Refusing: this is a batch member worktree. Members never open pull requests; the leader lands the member with batch-land and opens the final PR." >&2; \
		exit 1; \
	fi; \
	$(MAKE) -s agent-write-preflight; \
	CURRENT=$$(git branch --show-current); \
	case "$$CURRENT" in \
		develop|main|release) echo "Refusing to open PR from $$CURRENT — switch to a feature branch."; exit 1 ;; \
	esac; \
	if [ -n "$$PR_OPEN_BODY_FILE" ] && [ ! -f "$$PR_OPEN_BODY_FILE" ]; then \
		echo "PR_BODY_FILE does not exist: $$PR_OPEN_BODY_FILE" >&2; \
		exit 1; \
	fi; \
	REPOSITORY="$(or $(REPO),BenchBox-dev/BenchBox)"; \
	case "$$REPOSITORY" in \
		*/*/*|/*|*/|"") echo "REPO must be a GitHub owner/name identity" >&2; exit 2 ;; \
		*/*) ;; \
		*) echo "REPO must be a GitHub owner/name identity" >&2; exit 2 ;; \
	esac; \
	ORIGIN_URL=$$(git remote get-url --push origin) || { echo "Could not resolve the origin remote" >&2; exit 1; }; \
	case "$$ORIGIN_URL" in \
		git@github.com:*) ORIGIN_REPOSITORY="$${ORIGIN_URL#git@github.com:}" ;; \
		ssh://git@github.com/*) ORIGIN_REPOSITORY="$${ORIGIN_URL#ssh://git@github.com/}" ;; \
		https://github.com/*) ORIGIN_REPOSITORY="$${ORIGIN_URL#https://github.com/}" ;; \
		*) echo "origin remote is not a supported GitHub URL or SSH form" >&2; exit 1 ;; \
	esac; \
	ORIGIN_REPOSITORY="$${ORIGIN_REPOSITORY%.git}"; \
	ORIGIN_REPOSITORY="$${ORIGIN_REPOSITORY%/}"; \
	ORIGIN_OWNER="$${ORIGIN_REPOSITORY%%/*}"; \
	case "$$ORIGIN_REPOSITORY" in \
		*/*/*|/*|*/|"") echo "origin remote is not a GitHub owner/name identity" >&2; exit 1 ;; \
		*/*) ;; \
		*) echo "origin remote is not a GitHub owner/name identity" >&2; exit 1 ;; \
	esac; \
	HEAD_SPEC="$$CURRENT"; \
	ORIGIN_REPOSITORY_KEY=$$(printf '%s' "$$ORIGIN_REPOSITORY" | tr 'A-Z' 'a-z'); \
	TARGET_REPOSITORY_KEY=$$(printf '%s' "$$REPOSITORY" | tr 'A-Z' 'a-z'); \
	if [ "$$ORIGIN_REPOSITORY_KEY" != "$$TARGET_REPOSITORY_KEY" ]; then \
		HEAD_SPEC="$$ORIGIN_OWNER:$$CURRENT"; \
	fi; \
	git fetch origin develop --quiet; \
	STACK_BASE=origin/develop; \
	PARENT_ARGS=; \
	if [ -n "$$PR_OPEN_PARENT" ]; then \
		uv run -- python scripts/pr_open_rest.py --repo "$$REPOSITORY" --head-owner "$$ORIGIN_OWNER" --branch "$$CURRENT" \
			--head-spec "$$HEAD_SPEC" --parent "$$PR_OPEN_PARENT" --verify-parent || { echo "Refusing to open PR: PARENT is not a valid stack parent." >&2; exit 1; }; \
		git fetch origin "+refs/heads/$$PR_OPEN_PARENT:refs/remotes/origin/$$PR_OPEN_PARENT" --quiet || { echo "Refusing to open PR: cannot fetch the parent branch $$PR_OPEN_PARENT." >&2; exit 1; }; \
		STACK_BASE="origin/$$PR_OPEN_PARENT"; \
		PARENT_ARGS="--parent $$PR_OPEN_PARENT"; \
		git merge-base --is-ancestor "$$STACK_BASE" HEAD || { echo "Refusing to open PR: HEAD does not contain $$STACK_BASE. Rebase onto the parent first." >&2; exit 1; }; \
	elif ! git merge-base --is-ancestor origin/develop HEAD; then \
		if ! git merge-tree --write-tree origin/develop HEAD >/dev/null 2>&1; then \
			echo "Refusing to open PR: HEAD conflicts with origin/develop. Resolve the conflict first; no refresh merge is attempted." >&2; \
			exit 1; \
		fi; \
	fi; \
	CL_TITLE="$$PR_OPEN_TITLE"; \
	if [ -z "$$CL_TITLE" ]; then CL_TITLE=$$(git log --reverse --format=%s "$$STACK_BASE"..HEAD | sed -n 1p); fi; \
	CL_DIR=$$(mktemp -d); \
	git diff --name-only --diff-filter=ACDMRT "$$STACK_BASE"...HEAD > "$$CL_DIR/changed.txt"; \
	git diff --name-only --diff-filter=A "$$STACK_BASE"...HEAD > "$$CL_DIR/added.txt"; \
	uv run -- python scripts/changelog_fragments.py required --title "$$CL_TITLE" --changed-files-from "$$CL_DIR/changed.txt" --added-files-from "$$CL_DIR/added.txt" --release-base-sha "$$(git rev-parse origin/develop)" || { rm -rf "$$CL_DIR"; echo "Refusing to open PR: add a changelog fragment (make changelog-entry) or fix the title." >&2; exit 1; }; \
	rm -rf "$$CL_DIR"; \
	RECEIPT_FILE=$$(mktemp); \
	ALLOW_FILE=$$(mktemp); \
	trap 'rm -f "$$RECEIPT_FILE" "$$ALLOW_FILE"' EXIT; \
	git show HEAD:.github/preflight-known-failures.json > "$$ALLOW_FILE" 2>/dev/null || rm -f "$$ALLOW_FILE"; \
	python3 scripts/preflight_receipt.py verify --tree "$$(git rev-parse HEAD^{tree})" --allowlist "$$ALLOW_FILE" --summary-out "$$RECEIPT_FILE" || { echo "Refusing to open PR: no passing preflight receipt matches the tree being pushed. Nothing was pushed." >&2; exit 1; }; \
	TRUNK_MODE=$$(uv run -- python scripts/trunk_revert.py gate --branch "$$CURRENT" --repo "$$REPOSITORY"); \
	DRAFT_FLAG=; \
	if [ "$$TRUNK_MODE" = "draft" ] || [ -n "$$PR_OPEN_PARENT" ]; then DRAFT_FLAG=--draft; fi; \
	REVIEWED=$$(git rev-parse HEAD); \
	RECORD_FILE=$$(mktemp); \
	UNAVAILABLE_FILE=$$(mktemp); \
	trap 'rm -f "$$RECORD_FILE" "$$UNAVAILABLE_FILE" "$$RECEIPT_FILE" "$$ALLOW_FILE"' EXIT; \
	LOCAL_STATUS=0; \
	python3 scripts/oracle_local_review.py --policy .github/oracle-reviewers.yml --base-ref "$$STACK_BASE" --trusted-ref origin/develop --head "$$REVIEWED" --rebuttals "$$PR_OPEN_REBUTTALS" --adjudicate "$$PR_OPEN_ADJUDICATE" --record-out "$$RECORD_FILE" --unavailable-out "$$UNAVAILABLE_FILE" || LOCAL_STATUS=$$?; \
	UNAVAILABLE_CAUSE=; \
	if [ "$$LOCAL_STATUS" != 0 ]; then \
		if [ "$$LOCAL_STATUS" = 2 ]; then UNAVAILABLE_CAUSE=$$(sed -n 's/^availability: //p' "$$UNAVAILABLE_FILE" | sed -n 1p); fi; \
		if [ -z "$$UNAVAILABLE_CAUSE" ]; then echo "Refusing to open PR: the local oracle review did not clear this head. Nothing was pushed." >&2; exit 1; fi; \
		echo "The local oracle review is unavailable ($$UNAVAILABLE_CAUSE); opening as a draft. The hosted review decides." >&2; \
		DRAFT_FLAG=--draft; \
	fi; \
	$(MAKE) -s pr-conflict-scan BRANCH="$$CURRENT" REPO="$$REPOSITORY" || true; \
	git push origin "$$REVIEWED:refs/heads/$$CURRENT" || { echo "Push failed for $$CURRENT — aborting before opening a PR (remote branch may be stale)." >&2; exit 1; }; \
	git branch --set-upstream-to="origin/$$CURRENT" "$$CURRENT" >/dev/null 2>&1 || true; \
	PUBLISHED=$$(uv run -- python scripts/pr_open_rest.py --repo "$$REPOSITORY" --head-owner "$$ORIGIN_OWNER" \
		--branch "$$CURRENT" --head-spec "$$HEAD_SPEC" --title "$$PR_OPEN_TITLE" --body-file "$$PR_OPEN_BODY_FILE" --record-file "$$RECORD_FILE" --receipt-file "$$RECEIPT_FILE" $${UNAVAILABLE_CAUSE:+--local-review-unavailable "$$UNAVAILABLE_CAUSE"} $$DRAFT_FLAG $$PARENT_ARGS); \
	PR_STATUS="$${PUBLISHED%% *}"; \
	PR_NUMBER="$${PUBLISHED#* }"; \
	URL="$${PR_NUMBER#* }"; \
	PR_NUMBER="$${PR_NUMBER%% *}"; \
	REUSED_PR=0; \
	if [ "$$PR_STATUS" = "reused" ]; then \
		REUSED_PR=1; \
		echo "Reusing existing PR: $$URL"; \
	fi; \
	echo "$$URL" && \
	if [ -n "$$UNAVAILABLE_CAUSE" ]; then \
		echo "The local review was unavailable ($$UNAVAILABLE_CAUSE), so this PR is not armed and the hosted review-verdict decides. Mark it ready and arm it only after review-gate passes for this head."; \
	elif [ "$$TRUNK_MODE" = "draft" ] && [ "$$REUSED_PR" != "1" ]; then \
		echo "Opened as a draft because develop is red: mark it ready and arm it only after the owner authorizes this head (see docs/agent/merge-authority.md), or after develop is green."; \
	elif [ "$(READY)" != "1" ]; then \
		echo "Next: make pr-arm once the review fixes are pushed; auto-merge merges it when the required checks pass."; \
	elif [ -n "$(EVIDENCE)" ]; then \
		if [ "$$REUSED_PR" != "1" ]; then \
			echo "READY=1 with EVIDENCE applies to a reused PR, so this new PR is not armed."; \
			echo "Arm it after review with make pr-arm, or with the evidence transaction: make pr-ready REPO=\"$$REPOSITORY\" URL=\"$$URL\" HEAD=\"$$REVIEWED\" EVIDENCE=\"<readiness-evidence.json>\""; \
		else \
			$(MAKE) -s pr-ready REPO="$$REPOSITORY" URL="$$URL" HEAD="$$REVIEWED" EVIDENCE="$(EVIDENCE)" $(if $(BATCH),BATCH=1,); \
		fi; \
	else \
		$(MAKE) -s pr-arm PR="$$PR_NUMBER" REPO="$$REPOSITORY" HEAD="$$REVIEWED" $(if $(BATCH),BATCH=1,); \
	fi

.PHONY: pr-arm-auto-merge
pr-arm-auto-merge:
	@set -eu; \
	[ -n "$(EVIDENCE)" ] || { echo "EVIDENCE is required; use pr-landing-ready with exact readiness evidence" >&2; exit 2; }; \
	REPOSITORY="$(or $(REPO),BenchBox-dev/BenchBox)"; \
	CURRENT=$$(git branch --show-current); \
	PR_NUMBER="$(PR)"; \
	if [ -n "$(URL)" ]; then \
		if [ -n "$$PR_NUMBER" ]; then echo "PR and URL are mutually exclusive" >&2; exit 2; fi; \
		PR_NUMBER=$$(gh pr view --repo "$$REPOSITORY" "$(URL)" --json number --jq '.number') || { echo "Could not resolve URL to a PR in $$REPOSITORY" >&2; exit 1; }; \
	fi; \
	case "$$PR_NUMBER" in *[!0-9]*) echo "PR must resolve to a positive number" >&2; exit 2 ;; esac; \
	if [ -n "$$PR_NUMBER" ] && [ "$$PR_NUMBER" -le 0 ]; then echo "PR must resolve to a positive number" >&2; exit 2; fi; \
	[ -n "$$PR_NUMBER" ] || { echo "PR or URL is required" >&2; exit 2; }; \
	EXPECTED_HEAD="$(HEAD)"; \
	if [ -z "$$EXPECTED_HEAD" ]; then EXPECTED_HEAD=$$(git rev-parse HEAD); fi; \
	$(MAKE) -s pr-landing-ready REPO="$$REPOSITORY" PR="$$PR_NUMBER" HEAD="$$EXPECTED_HEAD" \
		EVIDENCE="$(EVIDENCE)" $(if $(BATCH),BATCH=1,) ARM=1

PR_ARM_PR_SET := $(if $(filter undefined,$(origin PR)),,1)
PR_ARM_HEAD_SET := $(if $(filter undefined,$(origin HEAD)),,1)
PR_ARM_REPO_SET := $(if $(filter undefined,$(origin REPO)),,1)
.PHONY: pr-rerun
pr-rerun: override PR := $(value PR)
pr-rerun: override REPO := $(value REPO)
pr-rerun: export PR_RERUN_PR := $(PR)
pr-rerun: export PR_RERUN_REPO := $(or $(REPO),BenchBox-dev/BenchBox)
pr-rerun:
	@uv run -- python scripts/pr_rerun.py

.PHONY: pr-arm
pr-arm: override PR := $(value PR)
pr-arm: override HEAD := $(value HEAD)
pr-arm: override REPO := $(value REPO)
pr-arm: export PR_ARM_PR := $(PR)
pr-arm: export PR_ARM_PR_SET := $(PR_ARM_PR_SET)
pr-arm: export PR_ARM_HEAD := $(HEAD)
pr-arm: export PR_ARM_HEAD_SET := $(PR_ARM_HEAD_SET)
pr-arm: export PR_ARM_REPO := $(REPO)
pr-arm: export PR_ARM_REPO_SET := $(PR_ARM_REPO_SET)
pr-arm: export PR_ARM_REQUIRE_BATCH := $(if $(BATCH),1,0)
pr-arm:
	@set -eu; \
	if [ -n "$${PR_ARM_PR:-}" ]; then \
		uv run -- python scripts/trunk_revert.py arm-gate --pr "$$PR_ARM_PR" --repo "$${PR_ARM_REPO:-BenchBox-dev/BenchBox}"; \
	else \
		uv run -- python scripts/trunk_revert.py arm-gate --branch "$$(git branch --show-current)" --repo "$${PR_ARM_REPO:-BenchBox-dev/BenchBox}"; \
	fi; \
	uv run -- python scripts/pr_arm.py

REMOTE_CLOSEOUT_TARGETS := pr-arm-remote pr-landing-remote-bind pr-landing-remote-record \
	pr-landing-remote-manifest pr-landing-remote-review pr-landing-remote-withdraw pr-landing-remote-ready
REMOTE_CLOSEOUT_VARIABLES := PR HEAD REPO BRANCH ITEM RECORD BUILDER_HEAD OWNER_EXPLAINED MODEL REVIEWER DRY_RUN RESUME
.PHONY: $(REMOTE_CLOSEOUT_TARGETS)
$(foreach name,$(REMOTE_CLOSEOUT_VARIABLES),$(eval $(REMOTE_CLOSEOUT_TARGETS): override $(name) := $$(value $(name))))
$(foreach name,$(REMOTE_CLOSEOUT_VARIABLES),$(eval $(REMOTE_CLOSEOUT_TARGETS): export REMOTE_CLOSEOUT_$(name) := $$($(name))))
$(REMOTE_CLOSEOUT_TARGETS): export PYTHONDONTWRITEBYTECODE := 1
REMOTE_ARM_GATE = if [ -n "$${REMOTE_CLOSEOUT_PR:-}" ]; then \
		uv run --no-project -- python scripts/trunk_revert.py arm-gate --pr "$$REMOTE_CLOSEOUT_PR" --repo "$${REMOTE_CLOSEOUT_REPO:-BenchBox-dev/BenchBox}"; \
	else \
		uv run --no-project -- python scripts/trunk_revert.py arm-gate --branch "$${REMOTE_CLOSEOUT_BRANCH:-$$(git branch --show-current)}" --repo "$${REMOTE_CLOSEOUT_REPO:-BenchBox-dev/BenchBox}"; \
	fi
pr-arm-remote:
	@set -eu; $(REMOTE_ARM_GATE); \
	uv run --no-project -- python scripts/remote_closeout.py arm
pr-landing-remote-bind:
	@uv run --no-project -- python scripts/remote_closeout.py bind
pr-landing-remote-record:
	@uv run --no-project -- python scripts/remote_closeout.py record
pr-landing-remote-manifest:
	@uv run --no-project -- python scripts/remote_closeout.py manifest
pr-landing-remote-review:
	@uv run --no-project -- python scripts/remote_review.py
pr-landing-remote-withdraw:
	@uv run --no-project -- python scripts/remote_closeout.py withdraw
pr-landing-remote-ready:
	@set -eu; $(REMOTE_ARM_GATE); \
	uv run --no-project -- python scripts/remote_closeout.py ready

.PHONY: trunk-revert
trunk-revert:
	@$(MAKE) -s agent-write-preflight
	@case "$(PR)" in ""|*[!0-9]*) echo "PR=<merged PR number> is required" >&2; exit 2 ;; esac
	@uv run -- python scripts/trunk_revert.py revert --pr "$(PR)" --repo "$(or $(REPO),BenchBox-dev/BenchBox)"

.PHONY: trunk-status
trunk-status:
	@uv run -- python scripts/trunk_revert.py status --repo "$(or $(REPO),BenchBox-dev/BenchBox)"

.PHONY: pr-ready
pr-ready:
	@[ -n "$(PR)" ] || [ -n "$(URL)" ] || { echo "PR or URL is required" >&2; exit 2; }
	@[ -n "$(HEAD)" ] || { echo "HEAD is required" >&2; exit 2; }
	@if [ -n "$(EVIDENCE)" ]; then \
		$(MAKE) -s pr-arm-auto-merge REPO="$(or $(REPO),BenchBox-dev/BenchBox)" PR="$(PR)" URL="$(URL)" HEAD="$(HEAD)" EVIDENCE="$(EVIDENCE)" $(if $(BATCH),BATCH=1,); \
	elif [ -n "$(URL)" ]; then \
		[ -z "$(PR)" ] || { echo "PR and URL are mutually exclusive" >&2; exit 2; }; \
		REPOSITORY="$(or $(REPO),BenchBox-dev/BenchBox)"; \
		PR_INFO=$$(gh pr view --repo "$$REPOSITORY" "$(URL)" --json number,url --jq '"\(.number) \(.url)"') || { echo "Could not resolve URL to a PR in $$REPOSITORY" >&2; exit 1; }; \
		PR_NUMBER="$${PR_INFO%% *}"; PR_URL=$$(printf '%s' "$${PR_INFO#* }" | tr 'A-Z' 'a-z'); \
		WANT=$$(printf '%s' "$$REPOSITORY" | tr 'A-Z' 'a-z'); \
		case "$$PR_URL" in "https://github.com/$$WANT/pull/"*) ;; *) echo "URL names a PR outside $$REPOSITORY; refusing to arm it" >&2; exit 2 ;; esac; \
		$(MAKE) -s pr-arm PR="$$PR_NUMBER" REPO="$$REPOSITORY" HEAD="$(HEAD)" $(if $(BATCH),BATCH=1,); \
	else \
		$(MAKE) -s pr-arm PR="$(PR)" REPO="$(or $(REPO),BenchBox-dev/BenchBox)" HEAD="$(HEAD)" $(if $(BATCH),BATCH=1,); \
	fi

.PHONY: shrink-rollup
shrink-rollup:
	@git fetch origin develop --quiet
	@uv run --project _project/scripts -- python _project/scripts/shrink_rollup.py

.PHONY: pr-fanout
pr-fanout:
	@ROLE=$$(git config --worktree --get benchbox.batch.role 2>/dev/null || true); \
	if [ "$$ROLE" = member ]; then \
		echo "Refusing: this is a batch member worktree. Members never open pull requests; the leader lands the member with batch-land and opens the final PR." >&2; \
		exit 1; \
	fi; \
	MAIN_CLONE=$$(dirname "$$(realpath "$$(git rev-parse --git-common-dir)")"); \
	TMP=$$(mktemp); \
	LOGDIR=$$(mktemp -d); \
	trap 'rm -f "$$TMP"; rm -rf "$$LOGDIR"' EXIT; \
	git worktree list --porcelain | sed -n 's/^worktree //p' | while IFS= read -r wt; do \
		[ "$$(realpath "$$wt")" = "$$MAIN_CLONE" ] && { echo "(skip $$wt: main clone)"; continue; }; \
		[ "$$(git -C "$$wt" config --worktree --get benchbox.batch.role 2>/dev/null || true)" = member ] && { echo "(skip $$wt: batch member)"; continue; }; \
		BR=$$(git -C "$$wt" branch --show-current 2>/dev/null); \
		case "$$BR" in develop|main|release|"") echo "(skip $$wt: branch=$$BR)"; continue ;; esac; \
		IDX=$$(($${IDX:-0} + 1)); \
		printf '%06d|%s\0' "$$IDX" "$$wt" >> "$$TMP"; \
	done; \
	if [ ! -s "$$TMP" ]; then exit 0; fi; \
	xargs -0 -n 1 -P "$(PR_FANOUT_JOBS)" sh -c 'logdir="$$1"; record="$$2"; idx="$${record%%|*}"; wt="$${record#*|}"; br=$$(git -C "$$wt" branch --show-current 2>/dev/null); { echo "==> $$wt [$$br]"; ( cd "$$wt" && $(MAKE) -s pr-open ) || { echo "(failed: $$wt)"; exit 1; }; } > "$$logdir/$$idx.log" 2>&1' sh "$$LOGDIR" < "$$TMP"; \
	STATUS=$$?; \
	for log in "$$LOGDIR"/*.log; do [ -e "$$log" ] && cat "$$log"; done; \
	exit $$STATUS

.PHONY: pr-refresh
pr-refresh:
	@CURRENT=$$(git branch --show-current); \
	case "$$CURRENT" in \
		develop|main|release|"") echo "Refusing to refresh $$CURRENT — switch to a feature branch worktree."; exit 1 ;; \
	esac; \
	git fetch origin develop --quiet && \
	git merge --no-edit origin/develop && \
	$(MAKE) -s pr-open

.PHONY: pr-conflict-scan
pr-conflict-scan:
	@CURRENT="$(BRANCH)"; \
	[ -n "$$CURRENT" ] || CURRENT=$$(git branch --show-current); \
	gh api --method GET --paginate "repos/$(or $(REPO),BenchBox-dev/BenchBox)/pulls?base=develop&state=open&per_page=100" \
		--jq '.[] | "\(.number) \(.head.ref)"' 2>/dev/null | \
	while read num branch; do \
		[ "$$branch" = "$$CURRENT" ] && continue; \
		git fetch origin "$$branch" --quiet 2>/dev/null || continue; \
		git rev-parse --verify --quiet "origin/$$branch^{commit}" >/dev/null || continue; \
		out=$$(git merge-tree --write-tree --name-only HEAD "origin/$$branch" 2>/dev/null); \
		[ $$? -eq 1 ] || continue; \
		files=$$(printf '%s\n' "$$out" | awk 'NR>1 && NF==0{exit} NR>1{printf "%s%s", sep, $$0; sep=", "}'); \
		echo "  ⚠ textual conflict with PR #$$num ($$branch) in $$files — coordinate before landing"; \
	done; true

.PHONY: pr-status
pr-status:
	@if [ "$(ALL_OPEN)" = "1" ] || [ "$(ALL_OPEN)" = "true" ] || [ "$(ALL_OPEN)" = "yes" ]; then \
		echo "All open develop PRs (bounded to $(PR_STATUS_ALL_OPEN_LIMIT)):"; \
		LIMIT="$(PR_STATUS_ALL_OPEN_LIMIT)"; \
	else \
		LIMIT="$(PR_STATUS_LIMIT)"; \
	fi; \
	gh pr list --base develop --state open --limit "$$LIMIT" --json number,title,headRefName,statusCheckRollup,autoMergeRequest \
		--template '{{range .}}#{{.number}} {{.title}} ({{.headRefName}}){{"\n"}}  auto-merge: {{if .autoMergeRequest}}ON{{else}}OFF{{end}}{{"\n"}}  checks: {{range .statusCheckRollup}}{{.name}}={{.conclusion}} {{end}}{{"\n\n"}}{{end}}'

.PHONY: pr-review-followups-list
pr-review-followups-list:
	@uv run --project _project/scripts -- python _project/scripts/pr_review_followups.py list \
		--base "$(PR_REVIEW_BASE)" \
		--limit-prs "$(PR_REVIEW_PR_LIMIT)" \
		--max-comments "$(PR_REVIEW_MAX_COMMENTS)" \
		$(if $(filter 1 true yes,$(PR_REVIEW_INCLUDE_RESOLVED)),--include-resolved) \
		$(if $(filter 1 true yes,$(PR_REVIEW_INCLUDE_POST_MERGE)),--include-post-merge) \
		$(if $(filter 1 true yes,$(PR_REVIEW_FAIL_ON_PENDING)),--fail-on-pending) \
		$(if $(PR_REVIEW_REPO),--repo "$(PR_REVIEW_REPO)") \
		$(if $(PR_REVIEW_SINCE),--since "$(PR_REVIEW_SINCE)") \
		$(if $(PR_REVIEW_UNTIL),--until "$(PR_REVIEW_UNTIL)")

.PHONY: pr-review-followups
pr-review-followups:
	@uv run --project _project/scripts -- python _project/scripts/pr_review_followups.py run \
		--base "$(PR_REVIEW_BASE)" \
		--limit-prs "$(PR_REVIEW_PR_LIMIT)" \
		--max-comments "$(PR_REVIEW_MAX_COMMENTS)" \
		--executor-sandbox "$(PR_REVIEW_EXECUTOR_SANDBOX)" \
		--executor-approval "$(PR_REVIEW_EXECUTOR_APPROVAL)" \
		$(if $(PR_REVIEW_REPO),--repo "$(PR_REVIEW_REPO)") \
		$(if $(PR_REVIEW_SINCE),--since "$(PR_REVIEW_SINCE)") \
		$(if $(PR_REVIEW_UNTIL),--until "$(PR_REVIEW_UNTIL)") \
		$(if $(PR_REVIEW_EXECUTOR_MODEL),--executor-model "$(PR_REVIEW_EXECUTOR_MODEL)") \
		$(if $(filter 1 true yes,$(PR_REVIEW_INCLUDE_POST_MERGE)),--include-post-merge) \
		$(if $(filter 0 false no,$(PR_REVIEW_REPLY)),--no-reply) \
		$(if $(filter 0 false no,$(PR_REVIEW_SUBMIT)),--no-submit) \
		$(if $(filter 1 true yes,$(PR_REVIEW_RESUME)),--resume)

.PHONY: dev-loop-metrics
dev-loop-metrics:
	@set -e; \
	TMP=$$(mktemp -d); \
	trap 'rm -rf "$$TMP"' EXIT; \
	SINCE=$$(uv run -- python -c 'from datetime import datetime, timedelta, timezone; print((datetime.now(timezone.utc) - timedelta(days=int("$(DEV_LOOP_METRICS_DAYS)"))).date().isoformat())'); \
	echo "Fetching develop-post-merge metrics since $$SINCE (limit $(DEV_LOOP_METRICS_LIMIT))..."; \
	RUN_IDS=$$(gh run list --workflow develop-post-merge.yml --branch develop --event push --created ">=$$SINCE" --limit "$(DEV_LOOP_METRICS_LIMIT)" --json databaseId --jq '.[].databaseId' 2>/dev/null || true); \
	for id in $$RUN_IDS; do \
		gh run download "$$id" -n metrics -D "$$TMP/$$id" >/dev/null 2>&1 || true; \
	done; \
	FILES=$$(find "$$TMP" -type f -name '*.json' | sort); \
	if [ -z "$$FILES" ]; then \
		echo "Metrics artifacts: 0"; \
		echo "PR-to-merged P50: n/a"; \
		echo "PR-to-merged P95: n/a"; \
		echo "Post-merge red rate: n/a (0/0)"; \
		echo "Conflict rate: n/a (0/0)"; \
		echo "runner minutes total: 0"; \
		exit 0; \
	fi; \
	jq -r -s ' \
		def nums($$k): [.[].[$$k] | select(type == "number")]; \
		def ceil_num: if . == floor then . else floor + 1 end; \
		def pct($$a; $$p): \
			if ($$a | length) == 0 then null \
			else ($$a | sort) as $$s | (((($$s | length) * $$p / 100) | ceil_num) - 1) as $$idx | $$s[$$idx] end; \
		def fmt: if . == null then "n/a" else tostring end; \
		def rate($$n; $$d): if $$d == 0 then "n/a" else (((100 * $$n / $$d) | tostring) + "%") end; \
		. as $$rows | \
		($$rows | length) as $$total | \
		(nums("pr_open_to_merged_seconds")) as $$merge_seconds | \
		([$$rows[] | select(.post_merge_red == true)] | length) as $$red | \
		([$$rows[] | select(.conflict_on_merge == true)] | length) as $$conflicts | \
		((nums("ci_runner_minutes") | add) // 0) as $$runner | \
		[ \
			"Metrics artifacts: \($$total)", \
			"PR-to-merged P50: \(pct($$merge_seconds; 50) | fmt) seconds", \
			"PR-to-merged P95: \(pct($$merge_seconds; 95) | fmt) seconds", \
			"Post-merge red rate: \(rate($$red; $$total)) (\($$red)/\($$total))", \
			"Conflict rate: \(rate($$conflicts; $$total)) (\($$conflicts)/\($$total))", \
			"runner minutes total: \($$runner)" \
		] | .[]' $$FILES
	@echo "---"; \
	echo "Dev-loop PR metrics (CI-failure baseline, see dev-loop-metrics-ci-failure-baseline-2):"; \
	test -f _project/scripts/dev_loop_pr_metrics.py && uv run -- python _project/scripts/dev_loop_pr_metrics.py --days "$(DEV_LOOP_METRICS_DAYS)" || true

CI_USAGE_OUTPUT ?= .cache/ci-usage-report/report-$(CI_USAGE_SINCE)_$(CI_USAGE_UNTIL).json

.PHONY: ci-usage-report
ci-usage-report:
	@test -n "$(CI_USAGE_SINCE)" -a -n "$(CI_USAGE_UNTIL)" || { echo "usage: make ci-usage-report CI_USAGE_SINCE=YYYY-MM-DD CI_USAGE_UNTIL=YYYY-MM-DD [CI_USAGE_OUTPUT=path] [CI_USAGE_ARGS=...]"; exit 2; }
	uv run -- python _project/scripts/dev_loop_pr_metrics.py --usage-report --since "$(CI_USAGE_SINCE)" --until "$(CI_USAGE_UNTIL)" --output "$(CI_USAGE_OUTPUT)" $(CI_USAGE_ARGS)
