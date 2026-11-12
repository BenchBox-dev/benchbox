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
override REASON := $(value REASON)
override ACCEPT_WRITES := $(value ACCEPT_WRITES)
override EXPECTED_OWNER := $(value EXPECTED_OWNER)
override EXPECTED_OWNER_GENERATION := $(value EXPECTED_OWNER_GENERATION)
override RELOCATE := $(value RELOCATE)

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
batch-member-check:
	@$(BATCH_SCRIPT) member-check --tests "$$BATCH_ARG_TESTS" \
		$${BATCH_ARG_PREFLIGHT_CMD:+--preflight-command "$$BATCH_ARG_PREFLIGHT_CMD"}

.PHONY: batch-land
batch-land: export BATCH_ARG_MEMBER := $(MEMBER)
batch-land: export BATCH_ARG_ACCEPT_WRITES := $(ACCEPT_WRITES)
batch-land:
	@test -n "$$BATCH_ARG_MEMBER" || { echo "Usage: make batch-land MEMBER=<id> [ACCEPT_WRITES=op_id,...]" >&2; exit 2; }
	@$(BATCH_SCRIPT) land --member "$$BATCH_ARG_MEMBER" --accept-writes "$$BATCH_ARG_ACCEPT_WRITES"

.PHONY: batch-advance
batch-advance: export BATCH_ARG_REASON := $(REASON)
batch-advance:
	@test -n "$$BATCH_ARG_REASON" || { echo "Usage: make batch-advance REASON=<fix|develop-merge>" >&2; exit 2; }
	@$(BATCH_SCRIPT) advance --reason "$$BATCH_ARG_REASON"

.PHONY: batch-owner-generation
batch-owner-generation:
	@$(BATCH_SCRIPT) owner-generation

.PHONY: batch-status
batch-status:
	@$(BATCH_SCRIPT) status

.PHONY: batch-verify
batch-verify:
	@$(BATCH_SCRIPT) verify

.PHONY: batch-evidence
batch-evidence:
	@$(BATCH_SCRIPT) evidence

override EXECUTE := $(value EXECUTE)

.PHONY: batch-finish
batch-finish: export BATCH_ARG_EXECUTE := $(EXECUTE)
batch-finish:
	@FLAG=""; case "$$BATCH_ARG_EXECUTE" in 1|true|yes) FLAG="--execute";; esac; \
	$(BATCH_SCRIPT) finish $$FLAG

.PHONY: batch-recover
batch-recover: export BATCH_ARG_BATCH := $(BATCH)
batch-recover: export BATCH_ARG_EXPECTED_OWNER := $(EXPECTED_OWNER)
batch-recover: export BATCH_ARG_EXPECTED_OWNER_GENERATION := $(EXPECTED_OWNER_GENERATION)
batch-recover: export BATCH_ARG_REASON := $(REASON)
batch-recover: export BATCH_ARG_RELOCATE := $(RELOCATE)
batch-recover:
	@test -n "$$BATCH_ARG_EXPECTED_OWNER" -a -n "$$BATCH_ARG_EXPECTED_OWNER_GENERATION" -a -n "$$BATCH_ARG_REASON" || { echo "Usage: make batch-recover EXPECTED_OWNER=<worker> EXPECTED_OWNER_GENERATION=<generation> REASON=<why> [RELOCATE=1] [BATCH=<id>]" >&2; exit 2; }
	@FLAG=""; case "$$BATCH_ARG_RELOCATE" in 1|true|yes) FLAG="--relocate";; esac; \
	$(BATCH_SCRIPT) recover --expected-owner "$$BATCH_ARG_EXPECTED_OWNER" --expected-owner-generation "$$BATCH_ARG_EXPECTED_OWNER_GENERATION" --reason "$$BATCH_ARG_REASON" \
		$${BATCH_ARG_BATCH:+--batch "$$BATCH_ARG_BATCH"} $$FLAG
