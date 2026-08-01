# TREEVIEW — weather-verdict

Complete directory tree of the live repo (`Desktop/mock projects/weather-verdict`),
generated 2026-07-31. **Nothing is omitted** from the listing itself; the only
exclusions are non-content artifacts: `.git/`, `__pycache__/`, `.ruff_cache/`,
`.cache/`, `.harness_opt/`, `.DS_Store`, and `*.pyc`.

Size context: `verdicts.db` ≈ 14 MB · `data/` ≈ 22 MB · `reports/` ≈ 19 MB ·
`ledger/` ≈ 900 KB · 99 test files (~820 tests) · 227 report entries ·
573 entries listed below.

```
weather-verdict/
├── .claude/
│   ├── skills/
│   │   └── harness-optimizer/
│   │       ├── assets/
│   │       │   └── proposer_skill_template.md
│   │       ├── references/
│   │       │   ├── anti_overfitting.md
│   │       │   ├── manual_mode.md
│   │       │   └── search_loop.md
│   │       ├── scripts/
│   │       │   ├── decontaminate.py
│   │       │   ├── init_filesystem.py
│   │       │   ├── run_search.py
│   │       │   ├── store_cli.py
│   │       │   └── validate_harness.py
│   │       └── SKILL.md
│   └── launch.json
├── .github/
│   └── workflows/
│       └── hk-accumulate.yml
├── archive/
│   └── weather_agent.py
├── backups/
│   └── verdicts.db.gz
├── config/
│   └── guard_cities.json
├── data/
│   ├── eglc_hourly_iem.jsonl
│   ├── eglc_hourly_iem.jsonl.gz
│   ├── ensemble_accumulate.csv
│   ├── hko_intraday.csv
│   ├── ksfo_cli_iem_10y.jsonl
│   ├── ksfo_hourly_iem.jsonl
│   ├── ksfo_hourly_iem.jsonl.gz
│   ├── oejn_hourly_iem.jsonl
│   ├── oejn_hourly_iem.jsonl.gz
│   ├── opkc_hourly_iem.jsonl
│   ├── opkc_hourly_iem.jsonl.gz
│   ├── wsss_hourly.jsonl
│   ├── wsss_hourly.jsonl.gz
│   ├── wsss_hourly_iem.jsonl
│   ├── wsss_hourly_iem.jsonl 2
│   ├── wsss_hourly_iem.jsonl.gz
│   ├── wsss_training.jsonl
│   ├── wsss_training.jsonl 2
│   └── wsss_training.jsonl.gz
├── docs/
│   ├── CODE_AUDIT_2026-07-15.md
│   ├── DRIVER_AUDIT.md
│   ├── INTRADAY_PROTOCOL.md
│   ├── NWP_LITERATURE_MAP.md
│   ├── OPUS_ADAPTATION_MANUAL.md
│   └── status.html
├── hooks/
│   └── pre-commit
├── ledger/
│   ├── preregistered/
│   │   ├── band_cover_market_modal.md
│   │   ├── conditional_dispersion_cloud.md
│   │   ├── cur_f_corroboration_guard_v2.md
│   │   ├── dispersion_inflation.md
│   │   ├── kalshi_austin_seam.md
│   │   ├── kalshi_expansion.md
│   │   ├── kalshi_s2a_kill_test.md
│   │   ├── kalshi_seattle_seam.md
│   │   ├── kalshi_sf_seam.md
│   │   ├── lock_state_season_calibration.md
│   │   ├── london_lock_instrumentation.md
│   │   ├── london_settlement_undershoot.md
│   │   ├── member_bias_break_watch.md
│   │   ├── p2_peak_conditioning.md
│   │   ├── p2b_1200_forward.md
│   │   ├── p3_dayahead_model.md
│   │   ├── persistent_decline_lock.md
│   │   ├── polymarket_tape_kill_test.md
│   │   ├── polymarket_tape_kill_test_v2.md
│   │   ├── postpeak_lag_trade.md
│   │   ├── postpeak_lag_trade_khi.md
│   │   ├── postpeak_lag_trade_ldn_jed.md
│   │   ├── postpeak_lag_trade_sf.md
│   │   ├── register_overread_gate.md
│   │   ├── served_number_campaign.md
│   │   ├── served_number_campaign_wp1.md
│   │   ├── served_number_campaign_wp2.md
│   │   ├── served_number_campaign_wp3.md
│   │   ├── served_number_campaign_wp4.md
│   │   ├── served_number_campaign_wp5.md
│   │   ├── served_number_campaign_wp6.md
│   │   ├── served_number_campaign_wp7.md
│   │   ├── sf_cli_scale_intraday_pmf.md
│   │   ├── sf_native_f_headline.md
│   │   ├── sf_verdict_blockers.md
│   │   ├── singapore_lock_certification.md
│   │   ├── singapore_pop_regime_split.md
│   │   ├── singapore_two_band.md
│   │   ├── twc_member_gate.md
│   │   ├── twc_offset.md
│   │   └── xref_analyst.md
│   ├── .gitignore
│   ├── candidates.json
│   ├── dead_candidates.jsonl
│   ├── finegrain_divergences.jsonl
│   ├── intraday_tape.jsonl
│   ├── kalshi_snapshots.jsonl
│   ├── kaus_cli_wu.jsonl
│   ├── ksfo_cli_direct.jsonl
│   ├── ksfo_cli_wu.jsonl
│   ├── mc_guard_validation.jsonl
│   ├── p2b_1200.jsonl
│   ├── singapore_lock.jsonl
│   ├── singapore_lock.lock
│   └── singapore_pop.jsonl
├── logs/
│   ├── .accumulate.lock
│   └── accumulate.log
├── reports/
│   ├── streams/
│   │   ├── hong_kong_high.csv
│   │   ├── hong_kong_low.csv
│   │   ├── kalshi_s2a_cache.jsonl
│   │   ├── london_high.csv
│   │   ├── london_low.csv
│   │   ├── pm_tape_cache.jsonl
│   │   └── pm_tape_cache_v2.jsonl
│   ├── _p2_probe.py
│   ├── _p3_stageA.py
│   ├── _sg_convective_nowcast.py
│   ├── accumulate-cron.log
│   ├── accumulate.launchd.err.log
│   ├── accumulate.launchd.out.log
│   ├── backtest_ar1.py
│   ├── backtest_cloud_scale.py
│   ├── backtest_conditional_dispersion.py
│   ├── backtest_dispersion.py
│   ├── backtest_emos_mean.py
│   ├── backtest_kalman.py
│   ├── backtest_kalshi_s2a.py
│   ├── backtest_polymarket_tape.py
│   ├── backtest_polymarket_tape_v2.py
│   ├── backtest_postpeak_lag.py
│   ├── backtest_postpeak_lag_v2.py
│   ├── backtest_recency_trend.py
│   ├── backtest_sf_native_f.py
│   ├── baseline.json
│   ├── bt_isotonic_recal.py
│   ├── c7_status.txt
│   ├── crossover_baseline.json
│   ├── crossover_now.json
│   ├── healthcheck_2026-06-06.txt
│   ├── healthcheck_2026-06-07.txt
│   ├── healthcheck_2026-06-08.txt
│   ├── healthcheck_2026-06-09.txt
│   ├── healthcheck_2026-06-10.txt
│   ├── healthcheck_2026-06-11.txt
│   ├── healthcheck_2026-06-12.txt
│   ├── healthcheck_2026-06-17.txt
│   ├── healthcheck_2026-06-20.txt
│   ├── healthcheck_2026-06-21.txt
│   ├── healthcheck_2026-06-24.txt
│   ├── healthcheck_2026-06-25.txt
│   ├── healthcheck_2026-06-26.txt
│   ├── healthcheck_2026-06-27.txt
│   ├── healthcheck_2026-06-28.txt
│   ├── healthcheck_2026-06-29.txt
│   ├── healthcheck_2026-06-30.txt
│   ├── healthcheck_2026-07-01.txt
│   ├── healthcheck_2026-07-02.txt
│   ├── healthcheck_2026-07-03.txt
│   ├── healthcheck_2026-07-04.txt
│   ├── healthcheck_2026-07-05.txt
│   ├── healthcheck_2026-07-06.txt
│   ├── healthcheck_2026-07-07.txt
│   ├── healthcheck_2026-07-08.txt
│   ├── healthcheck_2026-07-09.txt
│   ├── healthcheck_2026-07-10.txt
│   ├── healthcheck_2026-07-11.txt
│   ├── healthcheck_2026-07-12.txt
│   ├── healthcheck_2026-07-13.txt
│   ├── healthcheck_2026-07-14.txt
│   ├── healthcheck_2026-07-15.txt
│   ├── healthcheck_2026-07-16.txt
│   ├── healthcheck_2026-07-28.txt
│   ├── healthcheck_adjudication_2026-07-14.md
│   ├── healthcheck_status.json
│   ├── hko_april2026_observed_provisional.csv
│   ├── hko_june2026_observed_provisional.csv
│   ├── hko_may2026_observed_provisional.csv
│   ├── hko_observatory_daily_2021_2026.csv
│   ├── hong_kong_high.csv
│   ├── hong_kong_low.csv
│   ├── kalshi.launchd.err.log
│   ├── kalshi.launchd.out.log
│   ├── latest.txt
│   ├── launchd-verdict.err
│   ├── launchd-verdict.out
│   ├── launchd.err
│   ├── launchd.err.log
│   ├── launchd.out
│   ├── launchd.out.log
│   ├── live_crps_probe_SUCCESS.txt
│   ├── london_high.csv
│   ├── london_low.csv
│   ├── mc_verdict_sim_2026-07-27.json
│   ├── member_bias_ref.json
│   ├── probe_sf_cli_scale_2026-07-27.json
│   ├── server.log
│   ├── settlement_crosscheck.csv
│   ├── sf_intraday_backtest_10y_2026-07-25.txt
│   ├── sf_intraday_backtest_2026-07-25.txt
│   ├── sf_verdict_2026-07-25.txt
│   ├── sf_verdict_2026-07-25_1300.txt
│   ├── sf_verdict_2026-07-25_cli.txt
│   ├── tape.launchd.err.log
│   ├── tape.launchd.out.log
│   ├── treeview.launchd.err.log
│   ├── treeview.launchd.out.log
│   ├── truth_config.json
│   ├── twc_gate_2026-07-29.json
│   ├── twc_gate_2026-07-29.txt
│   ├── twc_probe.json
│   ├── verdict-singapore-2026-06-28-0430sgt.txt
│   ├── verdict-singapore-2026-06-28-1809sgt.txt
│   ├── verdict-singapore-2026-06-29-0900sgt.txt
│   ├── verdict-singapore-2026-06-29-1500sgt.txt
│   ├── verdict-singapore-2026-06-30-0900sgt.txt
│   ├── verdict-singapore-2026-06-30-1500sgt.txt
│   ├── verdict-singapore-2026-07-01-0900sgt.txt
│   ├── verdict-singapore-2026-07-01-1802sgt.txt
│   ├── verdict-singapore-2026-07-02-0907sgt.txt
│   ├── verdict-singapore-2026-07-02-1513sgt.txt
│   ├── verdict-singapore-2026-07-03-0110sgt.txt
│   ├── verdict-singapore-2026-07-03-0147sgt.txt
│   ├── verdict-singapore-2026-07-03-0900sgt.txt
│   ├── verdict-singapore-2026-07-03-1510sgt.txt
│   ├── verdict-singapore-2026-07-04-0900sgt.txt
│   ├── verdict-singapore-2026-07-04-1500sgt.txt
│   ├── verdict-singapore-2026-07-05-0900sgt.txt
│   ├── verdict-singapore-2026-07-05-1500sgt.txt
│   ├── verdict-singapore-2026-07-06-0900sgt.txt
│   ├── verdict-singapore-2026-07-06-1500sgt.txt
│   ├── verdict-singapore-2026-07-07-0900sgt.txt
│   ├── verdict-singapore-2026-07-07-1500sgt.txt
│   ├── verdict-singapore-2026-07-08-0900sgt.txt
│   ├── verdict-singapore-2026-07-08-1315sgt.txt
│   ├── verdict-singapore-2026-07-08-1503sgt.txt
│   ├── verdict-singapore-2026-07-08-1815sgt.txt
│   ├── verdict-singapore-2026-07-09-0900sgt.txt
│   ├── verdict-singapore-2026-07-09-1315sgt.txt
│   ├── verdict-singapore-2026-07-09-1509sgt.txt
│   ├── verdict-singapore-2026-07-09-1815sgt.txt
│   ├── verdict-singapore-2026-07-10-0900sgt.txt
│   ├── verdict-singapore-2026-07-10-1315sgt.txt
│   ├── verdict-singapore-2026-07-10-1500sgt.txt
│   ├── verdict-singapore-2026-07-10-1815sgt.txt
│   ├── verdict-singapore-2026-07-11-0905sgt.txt
│   ├── verdict-singapore-2026-07-11-1315sgt.txt
│   ├── verdict-singapore-2026-07-11-1500sgt.txt
│   ├── verdict-singapore-2026-07-11-1815sgt.txt
│   ├── verdict-singapore-2026-07-12-0900sgt.txt
│   ├── verdict-singapore-2026-07-12-1316sgt.txt
│   ├── verdict-singapore-2026-07-12-1500sgt.txt
│   ├── verdict-singapore-2026-07-12-1815sgt.txt
│   ├── verdict-singapore-2026-07-13-0900sgt.txt
│   ├── verdict-singapore-2026-07-13-1329sgt.txt
│   ├── verdict-singapore-2026-07-13-1500sgt.txt
│   ├── verdict-singapore-2026-07-13-1815sgt.txt
│   ├── verdict-singapore-2026-07-14-0900sgt.txt
│   ├── verdict-singapore-2026-07-14-1315sgt.txt
│   ├── verdict-singapore-2026-07-14-1502sgt.txt
│   ├── verdict-singapore-2026-07-14-1817sgt.txt
│   ├── verdict-singapore-2026-07-15-0900sgt.txt
│   ├── verdict-singapore-2026-07-15-1315sgt.txt
│   ├── verdict-singapore-2026-07-15-1500sgt.txt
│   ├── verdict-singapore-2026-07-15-1815sgt.txt
│   ├── verdict-singapore-2026-07-16-0900sgt.txt
│   ├── verdict-singapore-2026-07-16-1315sgt.txt
│   ├── verdict-singapore-2026-07-16-1500sgt.txt
│   ├── verdict-singapore-2026-07-16-1830sgt.txt
│   ├── verdict-singapore-2026-07-17-0900sgt.txt
│   ├── verdict-singapore-2026-07-17-1315sgt.txt
│   ├── verdict-singapore-2026-07-17-1500sgt.txt
│   ├── verdict-singapore-2026-07-17-1815sgt.txt
│   ├── verdict-singapore-2026-07-18-0900sgt.txt
│   ├── verdict-singapore-2026-07-18-1315sgt.txt
│   ├── verdict-singapore-2026-07-18-1500sgt.txt
│   ├── verdict-singapore-2026-07-18-1815sgt.txt
│   ├── verdict-singapore-2026-07-19-0900sgt.txt
│   ├── verdict-singapore-2026-07-19-1315sgt.txt
│   ├── verdict-singapore-2026-07-19-1500sgt.txt
│   ├── verdict-singapore-2026-07-19-1815sgt.txt
│   ├── verdict-singapore-2026-07-20-0900sgt.txt
│   ├── verdict-singapore-2026-07-20-1315sgt.txt
│   ├── verdict-singapore-2026-07-20-1500sgt.txt
│   ├── verdict-singapore-2026-07-20-1815sgt.txt
│   ├── verdict-singapore-2026-07-21-0900sgt.txt
│   ├── verdict-singapore-2026-07-21-1315sgt.txt
│   ├── verdict-singapore-2026-07-21-1500sgt.txt
│   ├── verdict-singapore-2026-07-21-1815sgt.txt
│   ├── verdict-singapore-2026-07-22-1430sgt.txt
│   ├── verdict-singapore-2026-07-22-1500sgt.txt
│   ├── verdict-singapore-2026-07-22-1815sgt.txt
│   ├── verdict-singapore-2026-07-23-0907sgt.txt
│   ├── verdict-singapore-2026-07-23-1322sgt.txt
│   ├── verdict-singapore-2026-07-23-1503sgt.txt
│   ├── verdict-singapore-2026-07-23-1815sgt.txt
│   ├── verdict-singapore-2026-07-24-0900sgt.txt
│   ├── verdict-singapore-2026-07-24-1317sgt.txt
│   ├── verdict-singapore-2026-07-24-1500sgt.txt
│   ├── verdict-singapore-2026-07-24-1828sgt.txt
│   ├── verdict-singapore-2026-07-25-0915sgt.txt
│   ├── verdict-singapore-2026-07-25-1317sgt.txt
│   ├── verdict-singapore-2026-07-25-1500sgt.txt
│   ├── verdict-singapore-2026-07-25-1823sgt.txt
│   ├── verdict-singapore-2026-07-26-0900sgt.txt
│   ├── verdict-singapore-2026-07-26-1320sgt.txt
│   ├── verdict-singapore-2026-07-26-1500sgt.txt
│   ├── verdict-singapore-2026-07-26-1815sgt.txt
│   ├── verdict-singapore-2026-07-27-0900sgt.txt
│   ├── verdict-singapore-2026-07-27-1315sgt.txt
│   ├── verdict-singapore-2026-07-27-1500sgt.txt
│   ├── verdict-singapore-2026-07-28-0900sgt.txt
│   ├── verdict-singapore-2026-07-28-1315sgt.txt
│   ├── verdict-singapore-2026-07-28-1504sgt.txt
│   ├── verdict-singapore-2026-07-28-1815sgt.txt
│   ├── verdict-singapore-2026-07-29-0900sgt.txt
│   ├── verdict-singapore-2026-07-29-1328sgt.txt
│   ├── verdict-singapore-2026-07-29-1815sgt.txt
│   ├── verdict-singapore-2026-07-30-0900sgt.txt
│   ├── verdict-singapore-2026-07-30-1315sgt.txt
│   ├── verdict-singapore-2026-07-30-1500sgt.txt
│   ├── verdict-singapore-2026-07-30-1815sgt.txt
│   ├── verdict-singapore-2026-07-31-0905sgt.txt
│   ├── verdict-singapore-2026-07-31-1329sgt.txt
│   ├── verdict-singapore-2026-07-31-1507sgt.txt
│   ├── verdict-singapore-2026-07-31-1815sgt.txt
│   ├── watchdog_2026-07-23.json
│   ├── watchdog_2026-07-24.json
│   ├── watchdog_2026-07-25.json
│   ├── watchdog_2026-07-26.json
│   ├── watchdog_2026-07-27.json
│   ├── watchdog_2026-07-28.json
│   ├── watchdog_2026-07-30.json
│   ├── watchdog_2026-07-31.json
│   └── weather_market_calibration.py
├── tests/
│   ├── test_ab_fold_gate.py
│   ├── test_austin.py
│   ├── test_band_market_flag.py
│   ├── test_book_logger.py
│   ├── test_book_snapshots_schema.py
│   ├── test_bucket_call.py
│   ├── test_bucket_contract.py
│   ├── test_bucket_verdict.py
│   ├── test_calibration.py
│   ├── test_calibration_gate.py
│   ├── test_clob_book.py
│   ├── test_config.py
│   ├── test_convergence.py
│   ├── test_council.py
│   ├── test_crosscheck_grain.py
│   ├── test_data_interpretation.py
│   ├── test_e2e.py
│   ├── test_edge.py
│   ├── test_ensemble_accumulate.py
│   ├── test_ensemble_verification.py
│   ├── test_eval_harness.py
│   ├── test_failures.py
│   ├── test_finegrain_read.py
│   ├── test_focus_capture_coverage.py
│   ├── test_gen_verify_inputs.py
│   ├── test_handoff_bundle.py
│   ├── test_healthcheck_link.py
│   ├── test_hko_intraday_accumulate.py
│   ├── test_improvement_analyzer.py
│   ├── test_index_html_escaping.py
│   ├── test_integrity_flags.py
│   ├── test_intraday.py
│   ├── test_intraday_ceiling.py
│   ├── test_intraday_grade.py
│   ├── test_intraday_tape.py
│   ├── test_jeddah.py
│   ├── test_kalshi_logger.py
│   ├── test_karachi.py
│   ├── test_ledger_schema.py
│   ├── test_lessons.py
│   ├── test_lineage_blend.py
│   ├── test_live_floor.py
│   ├── test_lock_logger.py
│   ├── test_loop.py
│   ├── test_low_market.py
│   ├── test_market.py
│   ├── test_mc_verdict_sim.py
│   ├── test_member_break.py
│   ├── test_obs_cache_guard.py
│   ├── test_outliers_set_aside.py
│   ├── test_paper_pnl.py
│   ├── test_pinned_city_lookup.py
│   ├── test_postmortem.py
│   ├── test_probe_sf_cli_scale.py
│   ├── test_provenance.py
│   ├── test_quantum_kernel.py
│   ├── test_recency_bias.py
│   ├── test_residual_kalman.py
│   ├── test_scoring.py
│   ├── test_seattle.py
│   ├── test_security.py
│   ├── test_server.py
│   ├── test_settle_station_budget.py
│   ├── test_settle_tz_early.py
│   ├── test_settlement_floor.py
│   ├── test_settlement_resolution.py
│   ├── test_settlement_verify.py
│   ├── test_sf_cli_seam_guard.py
│   ├── test_sf_intraday.py
│   ├── test_shadow.py
│   ├── test_significance.py
│   ├── test_singapore_pop_logger.py
│   ├── test_sources.py
│   ├── test_sources_twc.py
│   ├── test_stop_rule.py
│   ├── test_storage_hardening.py
│   ├── test_tape_logger.py
│   ├── test_tc_gate.py
│   ├── test_timescale.py
│   ├── test_tracked.py
│   ├── test_twc_crossref.py
│   ├── test_twc_forecast_logger.py
│   ├── test_twc_gate_score.py
│   ├── test_twc_independence.py
│   ├── test_twc_offset.py
│   ├── test_twc_tracking.py
│   ├── test_utc_now.py
│   ├── test_verdict_serializers.py
│   ├── test_verdict_stress.py
│   ├── test_verify_cli_archive.py
│   ├── test_verify_station_budget.py
│   ├── test_watchdog_spine.py
│   ├── test_weatherbit.py
│   ├── test_wp2_daily_max_localday.py
│   ├── test_wp5_unparsed_bucket.py
│   ├── test_wp6_compact_partition.py
│   ├── test_wu_api_key_env.py
│   ├── test_wu_throttle_propagation.py
│   └── test_wunderground_truth.py
├── tools/
│   ├── ab_backtest.py
│   ├── accumulate.py
│   ├── analog_drift_diag.py
│   ├── analog_shrink_backtest.py
│   ├── backfill_obs_history.py
│   ├── book_logger.py
│   ├── bucket_confidence_backtest.py
│   ├── build_training_table.py
│   ├── calibration_gate_run.py
│   ├── com.weatherverdict.accumulate.plist
│   ├── com.weatherverdict.healthcheck.plist
│   ├── com.weatherverdict.kalshi.plist
│   ├── com.weatherverdict.tape.plist
│   ├── com.weatherverdict.treeview.plist
│   ├── conditional_bucket_backtest.py
│   ├── daily-watchdog-cron.sh
│   ├── daily_healthcheck.py
│   ├── daily_verdict.py
│   ├── dev_server.py
│   ├── ensemble_accumulate.py
│   ├── eval_harness.py
│   ├── finegrain_read.py
│   ├── gen_verify_inputs.py
│   ├── hko_intraday_accumulate.py
│   ├── improvement_analyzer.py
│   ├── install_hooks.sh
│   ├── intraday_ceiling_backtest.py
│   ├── kalshi_logger.py
│   ├── ledger_schema.py
│   ├── lessons.py
│   ├── lineage_blend_run.py
│   ├── live_nwp_point.py
│   ├── lock_logger.py
│   ├── mc_verdict_sim.py
│   ├── member_break_watch.py
│   ├── p2b_1200_logger.py
│   ├── paper_pnl.py
│   ├── probe_sf_cli_scale.py
│   ├── provenance_audit.py
│   ├── quantum_backtest.py
│   ├── residual_kalman_run.py
│   ├── resolve_truth_sources.py
│   ├── settlement_audit.py
│   ├── shadow_score.py
│   ├── singapore_pop_logger.py
│   ├── stop_rule_run.py
│   ├── tape_logger.py
│   ├── timescale_sweep.py
│   ├── twc_forecast_logger.py
│   ├── twc_gate_score.py
│   ├── twc_independence.py
│   ├── twc_offset_report.py
│   ├── two_band_backfill.py
│   ├── update_treeview.py
│   ├── verify.py
│   ├── verify_cli_archive.py
│   ├── watchdog_core.py
│   └── wu_pattern.py
├── weather_council/
│   ├── __init__.py
│   ├── agents.py
│   ├── analog_shrink.py
│   ├── bucket_contract.py
│   ├── bucket_verdict.py
│   ├── calibration.py
│   ├── calibration_gate.py
│   ├── cli_seam.py
│   ├── clob_book.py
│   ├── compare.py
│   ├── convergence.py
│   ├── council.py
│   ├── edge.py
│   ├── ensemble_verification.py
│   ├── failures.py
│   ├── intraday.py
│   ├── intraday_ceiling.py
│   ├── intraday_grade.py
│   ├── intraday_tape.py
│   ├── lineage_blend.py
│   ├── loop.py
│   ├── market.py
│   ├── member_break.py
│   ├── observation.py
│   ├── postmortem.py
│   ├── provenance.py
│   ├── quantum_kernel.py
│   ├── recency_bias.py
│   ├── residual_kalman.py
│   ├── scoring.py
│   ├── seasonal.py
│   ├── security.py
│   ├── sources.py
│   ├── spread_skill.py
│   ├── station_offset.py
│   ├── stop_rule.py
│   ├── storage.py
│   ├── tc_gate.py
│   ├── timescale.py
│   └── twc_offset.py
├── .gitignore
├── ALL_CODE.txt
├── CHANGELOG.md
├── CLAUDE.md
├── CODE_AUDIT.md
├── FINDINGS.md
├── HANDOFF.md
├── HANDOFF_CODE_BUNDLE.txt
├── ISSUES_2026-07-12_INTRADAY_ACCURACY.md
├── KIMI_MOBILE_CARRYOVER.md
├── Makefile
├── PLAN_OWN_FORECAST.md
├── README.md
├── ROADMAP.md
├── SESSION_STATE.md
├── TREEVIEW.md
├── Weather Council.command
├── _make_handoff_bundle.py
├── index.html
├── records.jsonl
├── run.py
├── season_base_rates.json
├── server.py
├── verdicts.db
├── verdicts.db.bak.20260610_200809
├── verdicts.db.bak.cand53.20260617_024107
├── verdicts.db.bak.hko0608.201407
└── verify_skill.py
```
