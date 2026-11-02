BATCH_SCRIPT = uv run -- python scripts/batch_integration.py

override BATCH := $(value BATCH)
override MEMBERS := $(value MEMBERS)
override MEMBER := $(value MEMBER)
override SCOPE_JSON := $(value SCOPE_JSON)
override PROJECT_ID := $(value PROJECT_ID)
override WORKTREE_PATH := $(value WORKTREE_PATH)
override DEPENDENCIES := $(value DEPENDENCIES)
override CONFIRM_CUTOVER := $(value CONFIRM_CUTOVER)
override TESTS := $(value TESTS)
override PREFLIGHT_CMD := $(value PREFLIGHT_CMD)
override REVIEW_CMD := $(value REVIEW_CMD)
override REASON := $(value REASON)
override SUITE := $(value SUITE)

.PHONY: batch-start
batch-start: export BATCH_ARG_BATCH := $(BATCH)
batch-start: export BATCH_ARG_MEMBERS := $(MEMBERS)
batch-start: export BATCH_ARG_SCOPE_JSON := $(SCOPE_JSON)
batch-start: export BATCH_ARG_PROJECT_ID := $(PROJECT_ID)
batch-start: export BATCH_ARG_WORKTREE_PATH := $(WORKTREE_PATH)
batch-start: export BATCH_ARG_DEPENDENCIES := $(DEPENDENCIES)
batch-start: export BATCH_ARG_CONFIRM_CUTOVER := $(CONFIRM_CUTOVER)
batch-start:
	@test -n "$$BATCH_ARG_BATCH" -a -n "$$BATCH_ARG_MEMBERS" -a -n "$$BATCH_ARG_SCOPE_JSON" -a -n "$$BATCH_ARG_PROJECT_ID" || { echo "Usage: make batch-start BATCH=<id> MEMBERS=<a,b,c> SCOPE_JSON=<member-to-patterns.json> PROJECT_ID=<id> [WORKTREE_PATH=<path>] [DEPENDENCIES=<member=dep1+dep2,...>] [CONFIRM_CUTOVER=1]" >&2; exit 2; }
	@CUTOVER=""; case "$$BATCH_ARG_CONFIRM_CUTOVER" in 1|true|yes) CUTOVER="--confirm-cutover";; esac; \
	$(BATCH_SCRIPT) start --batch "$$BATCH_ARG_BATCH" --members "$$BATCH_ARG_MEMBERS" --scope-json "$$BATCH_ARG_SCOPE_JSON" --project-id "$$BATCH_ARG_PROJECT_ID" \
		$${BATCH_ARG_WORKTREE_PATH:+--worktree-path "$$BATCH_ARG_WORKTREE_PATH"} \
		$${BATCH_ARG_DEPENDENCIES:+--dependencies "$$BATCH_ARG_DEPENDENCIES"} \
		$$CUTOVER

.PHONY: batch-member-create
batch-member-create: export BATCH_ARG_MEMBER := $(MEMBER)
batch-member-create: export BATCH_ARG_WORKTREE_PATH := $(WORKTREE_PATH)
batch-member-create:
	@test -n "$$BATCH_ARG_MEMBER" || { echo "Usage: make batch-member-create MEMBER=<id> [WORKTREE_PATH=<path>]" >&2; exit 2; }
	@$(BATCH_SCRIPT) member-create --member "$$BATCH_ARG_MEMBER" $${BATCH_ARG_WORKTREE_PATH:+--worktree-path "$$BATCH_ARG_WORKTREE_PATH"}

.PHONY: batch-member-check
batch-member-check: export BATCH_ARG_TESTS := $(TESTS)
batch-member-check: export BATCH_ARG_PREFLIGHT_CMD := $(PREFLIGHT_CMD)
batch-member-check: export BATCH_ARG_REVIEW_CMD := $(REVIEW_CMD)
batch-member-check:
	@$(BATCH_SCRIPT) member-check --tests "$$BATCH_ARG_TESTS" \
		$${BATCH_ARG_PREFLIGHT_CMD:+--preflight-command "$$BATCH_ARG_PREFLIGHT_CMD"} \
		$${BATCH_ARG_REVIEW_CMD:+--review-command "$$BATCH_ARG_REVIEW_CMD"}

.PHONY: batch-land
batch-land: export BATCH_ARG_MEMBER := $(MEMBER)
batch-land: export BATCH_ARG_DEPENDENCIES := $(DEPENDENCIES)
batch-land:
	@test -n "$$BATCH_ARG_MEMBER" || { echo "Usage: make batch-land MEMBER=<id> [DEPENDENCIES=<a,b>]" >&2; exit 2; }
	@$(BATCH_SCRIPT) land --member "$$BATCH_ARG_MEMBER" $${BATCH_ARG_DEPENDENCIES:+--dependencies "$$BATCH_ARG_DEPENDENCIES"}

.PHONY: batch-advance
batch-advance: export BATCH_ARG_REASON := $(REASON)
batch-advance:
	@test -n "$$BATCH_ARG_REASON" || { echo "Usage: make batch-advance REASON=<fix|develop-merge>" >&2; exit 2; }
	@$(BATCH_SCRIPT) advance --reason "$$BATCH_ARG_REASON"

.PHONY: batch-status
batch-status:
	@$(BATCH_SCRIPT) status

.PHONY: batch-verify
batch-verify:
	@$(BATCH_SCRIPT) verify

.PHONY: batch-evidence
batch-evidence: export BATCH_ARG_SUITE := $(SUITE)
batch-evidence:
	@$(BATCH_SCRIPT) evidence $${BATCH_ARG_SUITE:+--suite "$$BATCH_ARG_SUITE"}
