SITE_INPUTS_OUT ?= site-inputs
.PHONY: site-inputs
site-inputs:
	@uv run -- python scripts/site_inputs.py build --out "$(SITE_INPUTS_OUT)" \
		$(if $(SITE_INPUTS_PROFILE),--profile "$(SITE_INPUTS_PROFILE)") \
		$(if $(SITE_INPUTS_CORE_SHA),--core-sha "$(SITE_INPUTS_CORE_SHA)") \
		$(if $(SITE_INPUTS_PARENT_SHA),--parent-core-sha "$(SITE_INPUTS_PARENT_SHA)") \
		$(if $(SITE_INPUTS_PARENT_SOURCE),--parent-source "$(SITE_INPUTS_PARENT_SOURCE)") \
		$(if $(SITE_INPUTS_CERTIFIED_BY),--certified-by "$(SITE_INPUTS_CERTIFIED_BY)")
	@uv run -- python scripts/site_inputs.py verify "$(SITE_INPUTS_OUT)"

SITE_INPUTS_CANDIDATE ?= candidate4
SITE_INPUTS_CANDIDATE_OUT ?= site-inputs-$(SITE_INPUTS_CANDIDATE)
.PHONY: site-inputs-candidate
site-inputs-candidate:
	@case "$(SITE_INPUTS_CANDIDATE)" in candidate4|candidate5) ;; *) echo "SITE_INPUTS_CANDIDATE must be candidate4 or candidate5" >&2; exit 2;; esac
	@uv run -- python scripts/site_inputs.py build --out "$(SITE_INPUTS_CANDIDATE_OUT)" --profile "$(SITE_INPUTS_CANDIDATE)" \
		$(if $(SITE_INPUTS_CORE_SHA),--core-sha "$(SITE_INPUTS_CORE_SHA)") \
		$(if $(SITE_INPUTS_PARENT_SHA),--parent-core-sha "$(SITE_INPUTS_PARENT_SHA)")
	@uv run -- python scripts/site_inputs.py verify "$(SITE_INPUTS_CANDIDATE_OUT)"
