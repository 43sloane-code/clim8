"""Tests for the live-register floor fusion (sources._fuse_live_floor) and the settlement
cross-check (lock_logger.settle_cross_check) — the two 07-04 feed fixes.

Pins the safety contract: fusion can only RAISE the floor; the 24h register counts only when
it exceeds yesterday's max (attribution guard); missing inputs are no-ops; and a settled day
whose banked register floor implies a higher bucket gets a loud divergence warning, never a
silent rewrite."""
import unittest

from weather_council.sources import _fuse_live_floor
from tools.lock_logger import settle_cross_check


class TestFuseLiveFloor(unittest.TestCase):
    def test_current_reading_raises_floor(self):
        floor, note = _fuse_live_floor(30.0, 90.0, None, None)      # 90F = 32.2C > 30.0
        self.assertAlmostEqual(floor, 32.222, places=2)
        self.assertIn("live now 90", note)

    def test_register_counts_only_when_above_yesterday(self):
        # yesterday's max 31.1C (88F); register 92F=33.3C > yesterday -> attributable, raises
        floor, note = _fuse_live_floor(32.2, 90.0, 92.0, 31.1)
        self.assertAlmostEqual(floor, 33.333, places=2)
        self.assertIn("24h-register 92", note)
        # yesterday's max 33.9C (93F); register 92F NOT above it -> may be yesterday's, ignored
        floor, note = _fuse_live_floor(32.2, 90.0, 92.0, 33.9)
        self.assertAlmostEqual(floor, 32.222, places=2)             # only the current reading

    def test_stale_register_predawn_not_attributed_to_today(self):
        # 2026-07-09 Singapore pre-dawn DEFECT: today only warmed to 27.2°C (current 81°F), but
        # the 24h register still holds YESTERDAY's 89°F peak — which clears the whole-°F-rounded
        # 88°F (31.1°C) yesterday row by pure granularity. It is 4.5°C above today's own freshest
        # evidence => unattributable carryover => must NOT floor today (else remaining-rise
        # projected an impossible ~37°C for a ~30°C day). Only the real 27.2°C obs floor survives.
        floor, note = _fuse_live_floor(27.2, 81.0, 89.0, 31.1)
        self.assertAlmostEqual(floor, 27.222, places=2)   # the 81°F current, NOT the 89°F (31.7°C) register
        self.assertNotIn("register", note or "")          # the stale carryover was rejected

    def test_register_at_peak_still_fuses_when_today_corroborates(self):
        # Guard the fix does not OVER-reject: at the peak today's current (91°F) is within a
        # between-obs spike of the 92°F register, so it IS attributable and still fuses — the
        # 07-04 lesson must survive the attribution gate.
        floor, note = _fuse_live_floor(32.2, 91.0, 92.0, 31.1)
        self.assertAlmostEqual(floor, 33.333, places=2)
        self.assertIn("24h-register 92", note)

    def test_register_phantom_capped_at_wu_daily_max(self):
        # 2026-07-09 Jeddah DEFECT (user-caught): v3 register read 102°F while WU's own daily-max
        # endpoint (and every hourly ob) topped at 100°F — a phantom that served 39 the contract
        # paid at 38. With wu_record_max_f=100, the register is capped to 100°F and cannot raise
        # the floor above today's real 100°F (37.78°C) run-max. Settles 38, not 39.
        import math
        floor, note = _fuse_live_floor(37.78, 99.0, 102.0, 37.78, wu_record_max_f=100.0)
        self.assertEqual(math.floor(floor + 0.5), 38)     # NOT 39 (the 102°F phantom is dropped)
        self.assertNotIn("register", note or "")          # register did not raise the floor

    def test_register_at_or_below_wu_daily_max_still_fuses(self):
        # Guard the cap does not OVER-reject: a register CORROBORATED by WU's daily-max (both 90°F)
        # is a real between-obs peak the hourly rows missed — it must still fuse (London 07-07).
        import math
        floor, note = _fuse_live_floor(31.0, 89.0, 90.0, 32.0, wu_record_max_f=90.0)
        self.assertEqual(math.floor(floor + 0.5), 32)     # 90°F register == WU daily-max -> fuses
        self.assertIsNotNone(note)

    def test_cur_f_leads_lagging_daily_max_endpoint(self):
        # 2026-07-11 Jeddah (user-caught): the v1 daily-max endpoint LAGGED at 97°F (=36) while the
        # v3 CURRENT reading held 98°F sustained and the market SETTLED 37. cur_f is uncapped and
        # must lead — the phantom cap on the register must NOT drag the lock back to the lagging
        # endpoint. runmax_c=36.11 (97°F hourly), cur_f=98, register=100, endpoint=97 -> fuses 37.
        import math
        floor, note = _fuse_live_floor(36.11, 98.0, 100.0, 35.0, wu_record_max_f=97.0)
        self.assertEqual(math.floor(floor + 0.5), 37)     # cur_f 98°F leads; NOT capped to 36
        self.assertIn("live now 98", note)
        # and the ceiling now honours cur_f: the register may lead UP TO the fresh current reading
        # (98°F) even though the endpoint lags at 97°F — never capped beneath a trusted current ob.
        floor2, _ = _fuse_live_floor(36.11, None, 98.0, 35.0, wu_record_max_f=97.0)
        self.assertAlmostEqual(floor2, 36.11, places=2)   # cur_f absent -> register still capped at 97°F endpoint

    def test_wp3_outage_fallback_cap_blocks_phantom(self):
        # WP-3: daily-max endpoint DOWN (wu_record None) but a recent daily max (100°F) supplied as the
        # fallback -> the 102°F phantom register is still capped to 100 -> settles 38, not 39, mid-outage.
        import math
        floor, note = _fuse_live_floor(37.78, 99.0, 102.0, 37.0,
                                       wu_record_max_f=None, cap_fallback_f=100.0)
        self.assertEqual(math.floor(floor + 0.5), 38)
        self.assertNotIn("ABSENT_OUTAGE", note or "")     # capped by the fallback, not uncapped

    def test_wp3_outage_no_fallback_declares_absent(self):
        # WP-3: endpoint down AND no fallback -> the register still fuses (uncapped) but the note
        # DECLARES ABSENT_OUTAGE so it is a watchdog-visible alarm, never a silent phantom.
        floor, note = _fuse_live_floor(32.2, 90.0, 92.0, 31.1,
                                       wu_record_max_f=None, cap_fallback_f=None)
        self.assertIn("ABSENT_OUTAGE", note)
        self.assertIn("24h-register 92", note)

    def test_wp3_healthy_parity_no_outage_marker(self):
        # WP-3: endpoint present -> identical phantom-cap behavior, no outage marker.
        import math
        floor, note = _fuse_live_floor(37.78, 99.0, 102.0, 37.78, wu_record_max_f=100.0)
        self.assertEqual(math.floor(floor + 0.5), 38)
        self.assertNotIn("ABSENT_OUTAGE", note or "")

    def test_declined_cur_does_not_relax_the_phantom_cap(self):
        # The relaxation is cur_f-gated: when the current reading has DECLINED below the endpoint,
        # the register is still capped at the endpoint (the 07-09 phantom guard is intact).
        import math
        floor, note = _fuse_live_floor(37.78, 95.0, 102.0, 37.0, wu_record_max_f=100.0)
        self.assertEqual(math.floor(floor + 0.5), 38)     # cur_f 95<endpoint 100 -> register capped 100 -> 38
        self.assertNotIn("register", note or "")

    def test_never_lowers_and_none_safe(self):
        floor, note = _fuse_live_floor(33.0, 88.0, 89.0, 30.0)      # both below the floor
        self.assertEqual((floor, note), (33.0, None))
        floor, note = _fuse_live_floor(None, 86.0, None, None)      # no history floor yet
        self.assertAlmostEqual(floor, 30.0, places=1)
        self.assertEqual(_fuse_live_floor(31.0, None, None, None), (31.0, None))
        # register present but yesterday unknown -> unattributable -> ignored (conservative)
        floor, _ = _fuse_live_floor(31.0, None, 95.0, None)
        self.assertEqual(floor, 31.0)


class TestSettleCrossCheck(unittest.TestCase):
    def test_divergence_warns_and_stamps(self):
        rows = [{"target_date": "2026-07-04", "hour": 15, "kind": "sharpened",
                 "running_max_c": 33.3, "settled_bucket": 32, "modal_bucket": 32},
                {"target_date": "2026-07-04", "hour": 14, "kind": "sharpened",
                 "running_max_c": 32.2, "settled_bucket": 32, "modal_bucket": 32}]
        w = settle_cross_check(rows)
        self.assertEqual(len(w), 1)
        self.assertIn("SETTLE DIVERGENCE 2026-07-04", w[0])
        self.assertIn("implies 33", w[0])
        self.assertEqual(rows[0]["register_bucket"], 33)            # stamped, not rewritten
        self.assertEqual(rows[0]["settled_bucket"], 32)             # settlement untouched

    def test_consistent_day_is_silent(self):
        rows = [{"target_date": "2026-07-03", "hour": 15, "kind": "sharpened",
                 "running_max_c": 31.1, "settled_bucket": 31, "modal_bucket": 31}]
        self.assertEqual(settle_cross_check(rows), [])
        self.assertNotIn("register_bucket", rows[0])


if __name__ == "__main__":
    unittest.main()


class TestDayState(unittest.TestCase):
    def test_holding_vs_declining_and_risk(self):
        from weather_council.intraday_ceiling import _day_state, state_late_risk
        holding = [(10, 30.0), (12, 32.2), (15, 32.2)]          # still AT the max at 15:00
        # CERTIFIED 2026-07-06 (persistent_decline_lock.md): "declining" needs the signal to
        # PERSIST — the last TWO reads below the floor. A single below-read is the false-
        # decline trap (07-04 EGLC: dip at 15:50 then a new max at 16:50) and stays HOLDING.
        one_dip = [(10, 30.0), (12, 32.2), (15, 30.6)]          # 1 read below -> trap, holding
        declin = [(10, 30.0), (12, 32.2), (14, 30.8), (15, 30.6)]   # 2 consecutive below
        self.assertEqual(_day_state(holding, 15), "holding")
        self.assertEqual(_day_state(one_dip, 15), "holding")
        self.assertEqual(_day_state(declin, 15), "declining")
        self.assertIsNone(_day_state([], 15))
        # leak-free state-conditional rate: 25 holding days, 5 of which climbed a bucket late
        hist = {}
        for k in range(25):
            rise = 1.0 if k < 5 else 0.0                        # 5/25 raise the bucket after 15
            hist[f"d{k}"] = [(12, 32.2), (15, 32.2), (17, 32.2 + rise)]
        self.assertAlmostEqual(state_late_risk(hist, 15, "holding", False), 0.2)
        self.assertIsNone(state_late_risk(hist, 15, "declining", False))   # thin cell -> None
        # season cell (CERTIFIED lock_state_season_calibration.md): dated keys, n>=30 in-season
        # -> the cell rate (July=JJA here: 10/40 raise); off-season months fall back state-only.
        sh = {}
        for k in range(40):
            rise = 1.0 if k < 10 else 0.0
            sh[f"2025-07-{(k % 28) + 1:02d}x{k}"[:10] if False else f"2025-07-{k+1:02d}"] =                 [(12, 32.2), (15, 32.2), (17, 32.2 + rise)]
        sh = {f"2025-0{7 if k < 28 else 8}-{(k % 28) + 1:02d}": [(12, 32.2), (15, 32.2), (17, 32.2 + (1.0 if k < 10 else 0.0))] for k in range(40)}
        self.assertAlmostEqual(state_late_risk(sh, 15, "holding", False, month=7), 10/40, places=2)
        self.assertAlmostEqual(state_late_risk(sh, 15, "holding", False, month=1), 10/40)  # DJF thin -> state-only


class TestBankedVsLeading(unittest.TestCase):
    """2026-07-12 Karachi vocabulary defect (user-caught): a live v3 cur_f (91°F = 32.8°C, bucket
    33) got fused into the running max and the headline read "33°C banked — PROVISIONAL", but 33
    is NOT on the settlement record — the WU daily-max endpoint and every hourly ob topped at 90°F
    (bucket 32). cur_f LEADING a lagging endpoint is intended (Jeddah 07-09/07-11); dressing that
    uncorroborated lead in observation-grade ("banked", 100%) vocabulary is the bug — exactly the
    London 07-11 over-read. The banked floor must be the corroborated max (32); the cur_f lead (33)
    must render "LEADING / uncorroborated", never "banked".  feedback_market_leads_lagging_wu_endpoint.md
    """

    def _karachi_ceiling(self, *, running_max_c, banked_c, cur_f, endpoint_f, modal_bucket, pmf):
        from weather_council.intraday_ceiling import IntradayCeiling
        return IntradayCeiling(
            kind="sharpened", city="Karachi, Pakistan", target="2026-07-12",
            sub_degree=False, grain="C", hour=12.5, running_max_c=running_max_c, n_rise=40,
            pmf=pmf, modal_bucket=modal_bucket, modal_prob=0.80,
            day_state="holding", state_late_risk=0.13,
            live_cur_f=cur_f, live_max24_f=cur_f, feed="wu+live",
            banked_running_max_c=banked_c, wu_daily_max_f=endpoint_f,
            source="Jinnah Intl OPKC (live IEM ASOS METAR, hourly) + live now 91°F")

    def test_cur_f_only_lead_flags_uncorroborated(self):
        # cur_f 91°F (32.78°C → 33) is +1°F above the daily-max endpoint 90°F (32.22°C → 32) and
        # above every hourly ob (obs run-max 32.0°C → 32). Nothing on the record corroborates 33.
        from weather_council.intraday_ceiling import banked_vs_leading
        c = self._karachi_ceiling(running_max_c=32.78, banked_c=32.22, cur_f=91.0,
                                  endpoint_f=90.0, modal_bucket=33, pmf=((33, 0.80), (34, 0.20)))
        split = banked_vs_leading(c)
        self.assertTrue(split["uncorroborated_lead"])
        self.assertEqual(split["banked_bucket"], 32)     # the corroborated, observation-grade floor
        self.assertEqual(split["led_bucket"], 33)        # the cur_f lead

    def test_render_says_leading_coinflip_not_banked(self):
        # THE contract (grade-driven since 2026-07-12 pm): the fused-cur_f bucket renders as an
        # unresolved LIVE COIN-FLIP — the lead is named, never dressed as banked, never locked,
        # and never dismissed (the D5/F2 correction: no "probable over-read" framing).
        import types
        from run import _bucket_call_lines
        c = self._karachi_ceiling(running_max_c=32.78, banked_c=32.22, cur_f=91.0,
                                  endpoint_f=90.0, modal_bucket=33, pmf=((33, 0.80), (34, 0.20)))
        v = types.SimpleNamespace(
            place=types.SimpleNamespace(label=lambda: "Karachi, Pakistan"),
            validation=types.SimpleNamespace(residuals_high=[]), high=32.0)
        text = "\n".join(_bucket_call_lines(v, c))
        self.assertIn("LEADING", text)
        self.assertIn("coin-flip", text)
        self.assertIn("32°C banked", text)          # the corroborated floor is 32, not 33
        self.assertNotIn("33°C banked", text)       # the cur_f lead is NEVER dressed as banked
        self.assertNotIn("LOCK", text)              # a live lead is never lockable
        self.assertIn("settling surface", text)     # H2: the record that pays headlines
        self.assertIn("90°F", text)

    def test_corroborated_cur_f_is_not_flagged(self):
        # Guard against over-flagging: when cur_f == the endpoint (both 90°F → bucket 32), the lead
        # is corroborated — no LEADING label, the banked floor is 32 (the 07-04/07-07 lead still fuses).
        import types
        from weather_council.intraday_ceiling import banked_vs_leading
        from run import _bucket_call_lines
        c = self._karachi_ceiling(running_max_c=32.22, banked_c=32.22, cur_f=90.0,
                                  endpoint_f=90.0, modal_bucket=32, pmf=((32, 0.85), (33, 0.15)))
        split = banked_vs_leading(c)
        self.assertFalse(split["uncorroborated_lead"])
        self.assertEqual(split["banked_bucket"], 32)
        v = types.SimpleNamespace(
            place=types.SimpleNamespace(label=lambda: "Karachi, Pakistan"),
            validation=types.SimpleNamespace(residuals_high=[]), high=32.0)
        text = "\n".join(_bucket_call_lines(v, c))
        self.assertNotIn("LEADING (uncorroborated", text)

    def test_no_live_fusion_returns_none(self):
        # A replay/backtest ceiling (no banked_running_max_c set) has no distinct banked figure to
        # draw -> None, so callers keep the plain running-max wording (v1 replays unchanged).
        from weather_council.intraday_ceiling import banked_vs_leading
        c = self._karachi_ceiling(running_max_c=32.78, banked_c=None, cur_f=None,
                                  endpoint_f=None, modal_bucket=33, pmf=((33, 1.0),))
        self.assertIsNone(banked_vs_leading(c))


class TestLondonRegisterConsult(unittest.TestCase):
    """Regression for the 2026-07-07 London settlement UNDERSHOOT (user-caught): EGLC hourly
    topped 31°C while the WU register caught 90°F and the market SETTLED 32. London was excluded
    from the live-register consult (_WU_INTRADAY = {'singapore'} only), so the lock served 31."""

    def test_london_now_in_register_consult(self):
        from weather_council.intraday_ceiling import _LIVE_REGISTER, _WU_INTRADAY
        self.assertIn("london", _LIVE_REGISTER)          # the fix: London consults the register
        self.assertIn("singapore", _LIVE_REGISTER)       # Singapore unchanged
        self.assertIn("singapore", _WU_INTRADAY)         # Singapore reads the WU hourly feed
        self.assertNotIn("london", _WU_INTRADAY)         # London's hourly BACKBONE stays IEM (whole-°C)

    def test_fusion_recovers_the_settled_32(self):
        import math
        from weather_council.sources import _fuse_live_floor
        # EGLC 07-07: IEM hourly 31.0°C, WU current 89°F, register 90°F, yesterday 32°C.
        floor, note = _fuse_live_floor(31.0, 89.0, 90.0, 32.0)
        self.assertEqual(math.floor(floor + 0.5), 32)    # was 31 (hourly only) -> now 32
        self.assertIsNotNone(note)
        # the current reading ALONE (a real station value) already recovers it
        floor2, _ = _fuse_live_floor(31.0, 89.0, None, 32.0)
        self.assertEqual(math.floor(floor2 + 0.5), 32)
