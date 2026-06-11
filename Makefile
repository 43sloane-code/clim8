# Stdlib-only local test gate. No third-party runners — matching the project's
# no-dependency rule. `make check` is what the pre-commit hook runs.
#
#   make test     network-free unit suite (tests/)
#   make selftest each module's standalone known-answer self-test
#   make check    both of the above — the full regression gate
#   make verify   plain-English black-box report (3 E2E scenarios + gate)
#   make install-hooks  copy hooks/ into .git/hooks (activates the gate)

PYTHON ?= python3
# Modules that ship a standalone `if __name__ == "__main__": _self_test()`.
SELFTEST_MODULES = scoring timescale loop quantum_kernel calibration

.PHONY: check test selftest verify install-hooks

check: test selftest

# Human-readable verification harness. `make verify` for the offline report;
# `make verify LIVE=1` to also hit the network across the full health-check basket.
verify:
	PYTHONPATH=. $(PYTHON) tools/verify.py $(if $(LIVE),--live,)

test:
	PYTHONPATH=. $(PYTHON) -m unittest discover -s tests

selftest:
	@for m in $(SELFTEST_MODULES); do \
		echo "--- self-test: weather_council.$$m ---"; \
		$(PYTHON) -m weather_council.$$m || exit 1; \
	done

install-hooks:
	@tools/install_hooks.sh
