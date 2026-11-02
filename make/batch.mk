BATCH_SCRIPT = uv run -- python scripts/batch_integration.py

BATCH_REFUSE_MEMBER = ROLE=$$(git config --worktree --get benchbox.batch.role 2>/dev/null || true); \
	if [ "$$ROLE" = member ]; then \
		echo "Refusing: this is a batch member worktree. Members never open pull requests; the leader lands the member with batch-land and opens the final PR." >&2; \
		exit 1; \
	fi

.PHONY: batch-start
batch-start:
	@test -n "$(BATCH)" -a -n "$(MEMBERS)" -a -n "$(SCOPE_JSON)" -a -n "$(PROJECT_ID)" || { echo "Usage: make batch-start BATCH=<id> MEMBERS=<a,b,c> SCOPE_JSON=<member-to-patterns.json> PROJECT_ID=<id> [WORKTREE_PATH=<path>] [CONFIRM_CUTOVER=1]" >&2; exit 2; }
	@$(BATCH_SCRIPT) start --batch "$(BATCH)" --members "$(MEMBERS)" --scope-json "$(SCOPE_JSON)" --project-id "$(PROJECT_ID)" \
		$(if $(WORKTREE_PATH),--worktree-path "$(WORKTREE_PATH)",) \
		$(if $(filter 1 true yes,$(CONFIRM_CUTOVER)),--confirm-cutover,)

.PHONY: batch-member-create
batch-member-create:
	@test -n "$(MEMBER)" || { echo "Usage: make batch-member-create MEMBER=<id> [WORKTREE_PATH=<path>]" >&2; exit 2; }
	@$(BATCH_SCRIPT) member-create --member "$(MEMBER)" $(if $(WORKTREE_PATH),--worktree-path "$(WORKTREE_PATH)",)

.PHONY: batch-member-check
batch-member-check:
	@$(BATCH_SCRIPT) member-check --tests "$(TESTS)" \
		$(if $(PREFLIGHT_CMD),--preflight-command "$(PREFLIGHT_CMD)",) \
		$(if $(REVIEW_CMD),--review-command "$(REVIEW_CMD)",)

.PHONY: batch-land
batch-land:
	@test -n "$(MEMBER)" || { echo "Usage: make batch-land MEMBER=<id> [DEPENDENCIES=<a,b>]" >&2; exit 2; }
	@$(BATCH_SCRIPT) land --member "$(MEMBER)" $(if $(DEPENDENCIES),--dependencies "$(DEPENDENCIES)",)

.PHONY: batch-advance
batch-advance:
	@test -n "$(REASON)" || { echo "Usage: make batch-advance REASON=<fix|develop-merge>" >&2; exit 2; }
	@$(BATCH_SCRIPT) advance --reason "$(REASON)"

.PHONY: batch-status
batch-status:
	@$(BATCH_SCRIPT) status

.PHONY: batch-verify
batch-verify:
	@$(BATCH_SCRIPT) verify

.PHONY: batch-evidence
batch-evidence:
	@$(BATCH_SCRIPT) evidence $(if $(SUITE),--suite "$(SUITE)",)
