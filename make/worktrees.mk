override BRANCH := $(value BRANCH)
override WORKTREE_PATH := $(value WORKTREE_PATH)
override CONTROLLER_KIND := $(value CONTROLLER_KIND)
override CONTROLLER_ID := $(value CONTROLLER_ID)
override EXPECTED_HEAD_OID := $(value EXPECTED_HEAD_OID)
override FORMAT := $(value FORMAT)

.PHONY: worktree-create
worktree-create: export BRANCH := $(BRANCH)
worktree-create: export WORKTREE_PATH := $(WORKTREE_PATH)
worktree-create: export CONTROLLER_KIND := $(CONTROLLER_KIND)
worktree-create: export CONTROLLER_ID := $(CONTROLLER_ID)
worktree-create:
	@$(BENCHBOX_MAKEFILE_ROOT)scripts/worktree_lifecycle.sh create

.PHONY: worktree-remove
worktree-remove: export WORKTREE_PATH := $(WORKTREE_PATH)
worktree-remove:
	@$(BENCHBOX_MAKEFILE_ROOT)scripts/worktree_lifecycle.sh remove

worktree-release: export WORKTREE_PATH := $(WORKTREE_PATH)
worktree-release:
	@$(BENCHBOX_MAKEFILE_ROOT)scripts/worktree_lifecycle.sh release

.PHONY: worktree-session-release
worktree-session-release: export WORKTREE_PATH := $(WORKTREE_PATH)
worktree-session-release:
	@$(BENCHBOX_MAKEFILE_ROOT)scripts/worktree_lifecycle.sh session-release

worktree-finish: export WORKTREE_PATH := $(WORKTREE_PATH)
worktree-finish: export EXPECTED_HEAD_OID := $(EXPECTED_HEAD_OID)
worktree-finish: export FORMAT := $(FORMAT)
worktree-finish:
	@$(BENCHBOX_MAKEFILE_ROOT)scripts/worktree_lifecycle.sh finish

.PHONY: worktree-reap
worktree-reap:
	@uv run --no-project -- python $(BENCHBOX_MAKEFILE_ROOT)scripts/worktree_reap.py $(if $(filter 1,$(value APPLY)),--apply,)

.PHONY: worktree-list
worktree-list:
	@git worktree list --porcelain
