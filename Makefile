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
# analog_shrink added 2026-07-15: its _self_test existed but was ORPHANED from
# the gate — 479 lines whose known answers could rot red without `make check`
# noticing. p2b's selftest is a CLI flag, wired below the loop.
SELFTEST_MODULES = scoring timescale loop quantum_kernel calibration analog_shrink

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
	@echo "--- self-test: tools/p2b_1200_logger (open accrual clock) ---"
	@PYTHONPATH=. $(PYTHON) tools/p2b_1200_logger.py --selftest || exit 1

install-hooks:
	@tools/install_hooks.sh
