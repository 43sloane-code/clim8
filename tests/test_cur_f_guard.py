"""KATs for the cur_f corroboration guard v2 — 9 exact-match KATs, no partial
certification (frozen design, ledger/preregistered/cur_f_corroboration_guard_v2.md, D5).

FIXTURE PROVENANCE (the prereg's MATERIAL DISCLOSURE): the system did not persist v3
read-sequences before ObsLog go-live, so the incident read-SEQUENCES + secondary
fields below are RECONSTRUCTED — K1/K2 from values captured live in-session
2026-07-11 with plausible synthesized secondaries, K3–K5 fully synthesized from the
verified settlement + incident narrative, K6 from the session-captured 2026-07-31
KSFO outputs. The fixture OUTCOMES are data-VERIFIED against the final WU records
(the prereg Phase 0 table + the K6 executed-incident entry): those outcomes are the
anchors being certified, not the reconstructed sequences.

The 9: K1 Jeddah 07-11 (CORROBORATED banks 37) · K2 London 07-11 (UNCORROBORATED →
27 with %, 28 annotation-only) · K5 Jeddah 07-09 (register cap → 38) · K3/K4
register-LEAD carried (register fuses unchanged) · K6 SF 2026-07-31 (74 frozen over
72 → UNCORROBORATED, floor stays 72, 74 annotation no %) · D2 stale-value/fresh-
timestamp · D3 converging bounds (2.0 vs 2.1, post-peak refused) · D4 adaptive
freshness fallback · fail-closed on corrupt/missing guard state.
"""
import datetime as dt
import os
import tempfile
import unittest
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from weather_council.guard import evaluate_cur_f_lead
from weather_council.guard import corroboration as corr
from weather_council.guard import obslog, provenance as prov, serving
from weather_council.intraday_ceiling import intraday_ceiling
from weather_council.market import _native_reading_int


# --------------------------------------------------------------------- fixtures

def _f2c(f):
    return (f - 32.0) * 5.0 / 9.0


class FakeSources:
    """The five fetch surfaces intraday_ceiling consults, replayed from a fixture."""

    def __init__(self, *, wu_native, obs_rows, cur_f, max24_f, valid_local,
                 secondaries, yesterday_max_c, daily_max_f=None,
                 daily_max_n=None):
        self._wu = wu_native
        self._obs = obs_rows            # list[(ts_iso, temp_c)]
        self._live = {"cur_f": cur_f, "max24_f": max24_f,
                      "valid_local": valid_local, "secondaries": secondaries}
        self._yday = yesterday_max_c
        self._dmax = ({"max_f": daily_max_f, "n_obs": daily_max_n}
                      if daily_max_f is not None else None)

    def fetch_metar_observations(self, icao, start, end, tz):
        return list(self._obs)

    def wunderground_hourly_observations(self, icao, start, end, tz):
        return list(self._obs)

    def wunderground_current_v3(self, icao):
        return dict(self._live)

    def wunderground_daily_series(self, icao, start, end, tz):
        return {start.isoformat(): (self._yday, self._yday - 8.0)}

    def wunderground_daily_max(self, icao, target, tz):
        return dict(self._dmax) if self._dmax else None


def _history(today_iso, *, peak_hour, base_c, days=30, wu_native=False):
    """30 strictly-earlier days whose final max is attained BY peak_hour (remaining
    rise 0), so the sharpened pmf concentrates 100% on the served floor bucket."""
    rows = []
    yy, mm, _dd = map(int, today_iso.split("-"))
    for i in range(days):
        d = (dt.date(yy, mm, 1) - dt.timedelta(days=i + 2)).isoformat()
        rows.append((f"{d}T{peak_hour - 5:02d}:00", base_c - 2.0))
        rows.append((f"{d}T{peak_hour:02d}:00", base_c))
    return rows


SEC_A = {"temperatureDewPoint": 55.0, "windSpeed": 8.0, "pressureMeanSeaLevel": 1015.0,
         "relativeHumidity": 60.0, "windDirection": 270.0}
SEC_B = dict(SEC_A, windSpeed=11.0, relativeHumidity=57.0)   # a LIVE refresh of SEC_A


def _ceiling(self, tmp, *, city, target, today_obs, cur_f, max24_f, valid_local,
             secondaries, yesterday_max_c, daily_max_f, guard_now,
             prior_reads=(), daily_max_n=20):
    """Run the LIVE intraday ceiling (now_hour=None -> the register consult + guard
    fire) against a fixture, with the guard's ObsLog redirected to tmp."""
    wu_native = city in ("Singapore", "San Francisco")
    peak_hour = max(int(ts[11:13]) for ts, _c in today_obs)
    obs = _history(target, peak_hour=peak_hour, base_c=34.0) + list(today_obs)
    src = FakeSources(wu_native=wu_native, obs_rows=obs, cur_f=cur_f,
                      max24_f=max24_f, valid_local=valid_local,
                      secondaries=secondaries, yesterday_max_c=yesterday_max_c,
                      daily_max_f=daily_max_f, daily_max_n=daily_max_n)
    olog = os.path.join(tmp, "obslog.jsonl")
    for r in prior_reads:
        obslog.append_read(city, target, r["ts_utc"], cur_f=r["cur_f"],
                           max24_f=None, valid_local=r["valid_local"],
                           secondaries=r["secondaries"], path=olog)
    place = SimpleNamespace(name=city)
    return intraday_ceiling(place, dt.date.fromisoformat(target), sources=src,
                            today=dt.date.fromisoformat(target),
                            obslog_path=olog, guard_now=guard_now)


# ------------------------------------------------------------------ incident KATs

class TestIncidentKATs(unittest.TestCase):
    def test_k1_jeddah_07_11_corroborated_banks_37(self):
        # VERIFIED outcome: settles 37 (hourly recorded 99°F at 16:00 — a real
        # re-heat; cur_f 98°F sustained at 16:06 & 16:16 LED the lagging hourly).
        # fresh ∧ sustained → CORROBORATED → cur_f banks 98°F = 36.67°C → bucket 37.
        with tempfile.TemporaryDirectory() as tmp:
            now = dt.datetime(2026, 7, 11, 16, 16, tzinfo=ZoneInfo("Asia/Riyadh"))
            c = _ceiling(self, tmp, city="Jeddah", target="2026-07-11",
                         today_obs=[("2026-07-11T10:00", 33.9),
                                    ("2026-07-11T15:00", 35.0)],   # 95°F, still lagging
                         cur_f=98.0, max24_f=99.0,
                         valid_local="2026-07-11T16:15:00+0300",
                         secondaries=SEC_B, yesterday_max_c=35.0,
                         daily_max_f=95.0, guard_now=now,
                         prior_reads=[{"ts_utc": "2026-07-11T13:06:00+00:00",
                                       "cur_f": 98.0,
                                       "valid_local": "2026-07-11T16:05:00+0300",
                                       "secondaries": SEC_A}])
            self.assertEqual(c.kind, "sharpened")
            self.assertEqual(c.guard_provenance, prov.CORROBORATED_NOWCAST)
            self.assertTrue(c.guard_corroborated)
            self.assertTrue(c.guard_fresh)
            self.assertTrue(c.guard_sustained)            # 2 reads, 10 min apart, live secondaries
            self.assertFalse(c.guard_converging)          # |98 − 95| = 3 > 2°F bound
            self.assertAlmostEqual(c.banked_running_max_c, _f2c(98.0), places=4)
            self.assertEqual(_native_reading_int(c.banked_running_max_c, "C", False), 37)
            self.assertEqual(c.modal_bucket, 37)          # banks 37 — the verified settle
            self.assertIsNone(c.guard_lead_c)             # a corroborated lead is not excluded

    def test_k2_london_07_11_uncorroborated_27_pct_28_annotation(self):
        # VERIFIED outcome: settles 27 (hourly plateaued 81°F 13:20→17:20, NEVER
        # re-heated; cur_f 83°F frozen at one valid_local). Single stale ts, record
        # flat → UNCORROBORATED → floor 27 with the % on 27; 28 annotation, no %.
        with tempfile.TemporaryDirectory() as tmp:
            now = dt.datetime(2026, 7, 11, 17, 20, tzinfo=ZoneInfo("Europe/London"))
            frozen = "2026-07-11T13:20:00+0100"
            c = _ceiling(self, tmp, city="London", target="2026-07-11",
                         today_obs=[(f"2026-07-11T{h:02d}:00", _f2c(81.0))
                                    for h in (13, 14, 15, 16, 17)],
                         cur_f=83.0, max24_f=81.0, valid_local=frozen,
                         secondaries=SEC_A, yesterday_max_c=27.5,
                         daily_max_f=81.0, guard_now=now,
                         prior_reads=[{"ts_utc": "2026-07-11T13:00:00+00:00",
                                       "cur_f": 83.0, "valid_local": frozen,
                                       "secondaries": SEC_A}])
            self.assertEqual(c.kind, "sharpened")
            self.assertEqual(c.guard_provenance, prov.UNCORROBORATED_NOWCAST)
            self.assertFalse(c.guard_fresh)               # valid_local frozen 4h > 45min
            self.assertFalse(c.guard_sustained)           # identical payload — ONE stale read
            self.assertTrue(c.guard_converging)           # |83−81| ≤ 2 pre-peak — but ∧ fresh fails
            self.assertAlmostEqual(c.running_max_c, _f2c(81.0), places=4)   # base stays 81°F
            self.assertAlmostEqual(c.banked_running_max_c, _f2c(81.0), places=4)
            self.assertEqual(c.modal_bucket, 27)
            self.assertEqual(c.modal_prob, 1.0)           # the % is on 27
            self.assertTrue(all(b == 27 for b, _p in c.pmf))   # NO 28 bucket in the pmf
            self.assertEqual(_native_reading_int(c.guard_lead_c, "C", False), 28)
            # Gate 2: 28 renders as annotation carrying NO percentage.
            from run import _ceiling_lines
            text = "\n".join(_ceiling_lines(c))
            self.assertIn("GUARD", text)
            self.assertIn("28°C", text)
            self.assertIn("no %", text)
            self.assertIsNone(prov.served_prob(prov.UNCORROBORATED_NOWCAST, 0.9))
            self.assertIsNone(serving.bucket_prob(prov.UNCORROBORATED_NOWCAST, 0.9))
            ann = serving.lead_annotation(prov.UNCORROBORATED_NOWCAST, 28, "°C")
            self.assertEqual(ann["prob"], None)

    def test_k5_jeddah_07_09_register_cap_path_38(self):
        # VERIFIED outcome: settles 38 (hourly peak 100°F at 10:00 MORNING; the 102°F
        # phantom was the max24 REGISTER, post-peak while declining). cur_f ≈99 < 100
        # doesn't bank (no lead); the register cap (2eafce1/6533fca) holds 100 → 38.
        with tempfile.TemporaryDirectory() as tmp:
            now = dt.datetime(2026, 7, 9, 15, 5, tzinfo=ZoneInfo("Asia/Riyadh"))
            c = _ceiling(self, tmp, city="Jeddah", target="2026-07-09",
                         today_obs=[("2026-07-09T10:00", _f2c(100.0)),
                                    ("2026-07-09T15:00", 36.1)],
                         cur_f=99.0, max24_f=102.0,
                         valid_local="2026-07-09T15:04:00+0300",
                         secondaries=SEC_A, yesterday_max_c=_f2c(100.0),
                         daily_max_f=100.0, guard_now=now)
            self.assertEqual(c.kind, "sharpened")
            self.assertEqual(c.guard_provenance, prov.RECORDED)   # cur_f 99 < record 100: no lead
            self.assertIsNone(c.guard_lead_c)
            self.assertAlmostEqual(c.running_max_c, _f2c(100.0), places=4)  # phantom 102 capped
            self.assertEqual(c.modal_bucket, 38)          # the register cap path — unchanged
            self.assertEqual(c.modal_prob, 1.0)

    def test_k3_k4_register_lead_carried_unchanged(self):
        # Carried register-LEAD cases (a42ffa2/6533fca): the max24 register — NOT
        # cur_f — leads the lagging rows. The guard is not the register's object:
        # the path fuses byte-identical, provenance RECORDED.
        with tempfile.TemporaryDirectory() as tmp:
            # K3 Singapore 07-04: rows 91°F, register 92 corroborated by endpoint 92 → 33.
            now = dt.datetime(2026, 7, 4, 14, 5, tzinfo=ZoneInfo("Asia/Singapore"))
            c3 = _ceiling(self, tmp, city="Singapore", target="2026-07-04",
                          today_obs=[("2026-07-04T09:00", 31.0),
                                     ("2026-07-04T13:00", _f2c(91.0)),
                                     ("2026-07-04T14:00", 32.5)],
                          cur_f=91.0, max24_f=92.0,
                          valid_local="2026-07-04T14:04:00+0800",
                          secondaries=SEC_A, yesterday_max_c=_f2c(88.0),
                          daily_max_f=92.0, guard_now=now)
            self.assertEqual(c3.modal_bucket, 33)
            self.assertEqual(c3.guard_provenance, prov.RECORDED)
            self.assertIsNone(c3.guard_lead_c)
            self.assertIn("24h-register 92", c3.source)   # the register still fuses
        with tempfile.TemporaryDirectory() as tmp:
            # K4 London 07-07: IEM hourly 31.0°C, register 90 == endpoint 90 → 32.
            now = dt.datetime(2026, 7, 7, 14, 5, tzinfo=ZoneInfo("Europe/London"))
            c4 = _ceiling(self, tmp, city="London", target="2026-07-07",
                          today_obs=[("2026-07-07T10:00", 29.0),
                                     ("2026-07-07T14:00", 31.0)],
                          cur_f=89.0, max24_f=90.0,
                          valid_local="2026-07-07T14:04:00+0100",
                          secondaries=SEC_A, yesterday_max_c=32.0,
                          daily_max_f=90.0, guard_now=now)
            self.assertEqual(c4.modal_bucket, 32)         # was 31 hourly-only; register closes it
            self.assertEqual(c4.guard_provenance, prov.RECORDED)
            self.assertIsNone(c4.guard_lead_c)

    def test_k6_sanfrancisco_2026_07_31_uncorroborated_74_over_72(self):
        # THE incident specimen (executed 2026-07-31, session-captured): WU v3 cur_f
        # printed 74°F ~12:00–14:00 PDT on a FROZEN valid_local while the settlement
        # record (hourly obs, METARs, daily-max endpoint) never exceeded 72°F.
        # Pre-peak and |74−72| = 2°F meets the converging bound, BUT liveness fails
        # (single stale read) and freshness fails (frozen stamp) → UNCORROBORATED →
        # the floor/pmf base stay 72; 74 is annotation with NO %.
        with tempfile.TemporaryDirectory() as tmp:
            now = dt.datetime(2026, 7, 31, 14, 5, tzinfo=ZoneInfo("America/Los_Angeles"))
            frozen = "2026-07-31T12:00:00-0700"
            c = _ceiling(self, tmp, city="San Francisco", target="2026-07-31",
                         today_obs=[(f"2026-07-31T{h:02d}:00", _f2c(f))
                                    for h, f in ((9, 66.0), (11, 69.0),
                                                 (12, 72.0), (14, 72.0))],
                         cur_f=74.0, max24_f=72.0, valid_local=frozen,
                         secondaries=SEC_A, yesterday_max_c=_f2c(70.0),
                         daily_max_f=72.0, guard_now=now,
                         prior_reads=[{"ts_utc": "2026-07-31T19:05:00+00:00",
                                       "cur_f": 74.0, "valid_local": frozen,
                                       "secondaries": SEC_A}])
            self.assertEqual(c.kind, "sharpened")
            self.assertEqual(c.grain, "F")
            self.assertEqual(c.guard_provenance, prov.UNCORROBORATED_NOWCAST)
            self.assertFalse(c.guard_fresh)               # stamp frozen ~2h > window
            self.assertFalse(c.guard_sustained)           # single stale read — liveness fails
            self.assertTrue(c.guard_converging)           # |74−72| = 2 ≤ 2, pre-peak (14:05 < 17:00)
            self.assertFalse(c.guard_corroborated)        # fresh ∧ (…): fails
            self.assertAlmostEqual(c.running_max_c, _f2c(72.0), places=4)   # base STAYS 72
            self.assertAlmostEqual(c.banked_running_max_c, _f2c(72.0), places=4)
            self.assertEqual(c.modal_bucket, 72)
            self.assertEqual(c.modal_prob, 1.0)           # the served % sits on 72
            self.assertTrue(all(b == 72 for b, _p in c.pmf))   # no 74 bucket in the pmf
            self.assertEqual(_native_reading_int(c.guard_lead_c, "F", False), 74)
            from run import _ceiling_lines
            text = "\n".join(_ceiling_lines(c))
            self.assertIn("74°F", text)                   # named as annotation
            self.assertIn("no %", text)                   # never dressed in a percentage
            self.assertNotIn("74°F 9", text)              # and no pmf mass prints on it
            # The shadow decision was emitted for this serve (prereg Phase 5).
            shadow = os.path.join(tmp, "obslog.jsonl.shadow")
            with open(shadow, encoding="utf-8") as fh:
                rows = [obslog.json.loads(l) for l in fh]
            self.assertEqual(rows[-1]["provenance"], prov.UNCORROBORATED_NOWCAST)


# -------------------------------------------------------------- adversarial KATs

class TestAdversarialKATs(unittest.TestCase):
    """Synthetic adversarial KATs (D2/D3/D4 + fail-closed) — pure feed-logic
    replays through evaluate_cur_f_lead, SF/KSFO config."""

    ICAO = "KSFO"
    NOW = dt.datetime(2026, 7, 15, 12, 15, tzinfo=ZoneInfo("America/Los_Angeles"))

    def _eval(self, *, cur_f, valid_local, reads, recorded_f, inter_obs=None,
              now=None, icao=None, fused_with=30.0, fused_without=28.3333):
        return evaluate_cur_f_lead(
            icao=icao or self.ICAO, cur_f=cur_f, valid_local=valid_local,
            reads=reads, now_local=now or self.NOW, inter_obs_min=inter_obs,
            recorded_max_c=_f2c(recorded_f),
            fused_with_cur_c=fused_with, fused_without_cur_c=fused_without)

    def test_d2_liveness_defeats_stale_value_fresh_timestamp(self):
        # D2: WU re-serves the SAME payload on a fresh receipt (stale value, fresh
        # timestamp). Receipts 15 min apart, valid_local refreshing — but every
        # secondary IDENTICAL => it is ONE observation, not two => not sustained.
        # Gap 3°F (>2) so only sustainment could corroborate.
        reads = [
            {"ts_utc": "2026-07-15T19:00:00+00:00", "cur_f": 86.0,
             "valid_local": "2026-07-15T11:58:00-0700", "secondaries": dict(SEC_A)},
            {"ts_utc": "2026-07-15T19:15:00+00:00", "cur_f": 86.0,
             "valid_local": "2026-07-15T12:14:00-0700", "secondaries": dict(SEC_A)},
        ]
        r = self._eval(cur_f=86.0, valid_local="2026-07-15T12:14:00-0700",
                       reads=reads, recorded_f=83.0, inter_obs=[20.0] * 12)
        self.assertTrue(r.fresh)                          # the receipt IS fresh — not the point
        self.assertFalse(r.sustained)                     # liveness fails: identical secondaries
        self.assertFalse(r.converging)                    # |86−83| = 3 > 2
        self.assertEqual(r.provenance, prov.UNCORROBORATED_NOWCAST)
        self.assertEqual(r.served_running_max_c, 28.3333)  # the lead is stripped
        # Control: a genuinely LIVE refresh (secondaries differ) IS sustained → banks.
        live = [reads[0], dict(reads[1], secondaries=dict(SEC_B))]
        r2 = self._eval(cur_f=86.0, valid_local="2026-07-15T12:14:00-0700",
                        reads=live, recorded_f=83.0, inter_obs=[20.0] * 12)
        self.assertTrue(r2.sustained)
        self.assertEqual(r2.provenance, prov.CORROBORATED_NOWCAST)
        self.assertEqual(r2.served_running_max_c, 30.0)
        self.assertAlmostEqual(r2.banked_running_max_c, _f2c(86.0), places=4)

    def test_d3_converging_bounds(self):
        # D3: the converging bound is exact — |gap| ≤ 2.0°F AND pre-peak; anything
        # more, or anything post-peak, is refused. Single fresh reads (no sustainment).
        fresh_ts = "2026-07-15T12:14:00-0700"
        one = [{"ts_utc": "2026-07-15T19:15:00+00:00", "cur_f": 74.0,
                "valid_local": fresh_ts, "secondaries": dict(SEC_A)}]
        # 2.0°F exactly, pre-peak (12:15 < 17:00) -> converging -> CORROBORATED.
        r = self._eval(cur_f=74.0, valid_local=fresh_ts, reads=one, recorded_f=72.0,
                       inter_obs=[20.0] * 12, fused_with=_f2c(74.0),
                       fused_without=_f2c(72.0))
        self.assertTrue(r.converging)
        self.assertEqual(r.provenance, prov.CORROBORATED_NOWCAST)
        self.assertAlmostEqual(r.served_running_max_c, _f2c(74.0), places=4)
        # 2.1°F -> outside the bound -> UNCORROBORATED.
        r = self._eval(cur_f=74.1, valid_local=fresh_ts, reads=one, recorded_f=72.0,
                       inter_obs=[20.0] * 12, fused_with=_f2c(74.1),
                       fused_without=_f2c(72.0))
        self.assertFalse(r.converging)
        self.assertEqual(r.provenance, prov.UNCORROBORATED_NOWCAST)
        self.assertAlmostEqual(r.served_running_max_c, _f2c(72.0), places=4)
        # 2.0°F but POST-peak (17:30 ≥ window end 17:00) -> converging unavailable.
        late = dt.datetime(2026, 7, 15, 17, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        r = self._eval(cur_f=74.0, valid_local=fresh_ts, reads=one, recorded_f=72.0,
                       inter_obs=[20.0] * 12, now=late, fused_with=_f2c(74.0),
                       fused_without=_f2c(72.0))
        self.assertFalse(r.converging)
        self.assertEqual(r.provenance, prov.UNCORROBORATED_NOWCAST)
        self.assertAlmostEqual(r.served_running_max_c, _f2c(72.0), places=4)

    def test_d4_adaptive_freshness_fallback(self):
        # D4: clamp(1.5 × trailing-median inter-obs, 10, 45); <12 intervals -> the
        # 45-min ceiling with basis=fallback.
        self.assertEqual(corr.freshness_window_min([]), (45.0, "fallback"))
        self.assertEqual(corr.freshness_window_min([20.0] * 5), (45.0, "fallback"))
        self.assertEqual(corr.freshness_window_min([20.0] * 12), (30.0, "adaptive"))
        self.assertEqual(corr.freshness_window_min([5.0] * 12), (10.0, "adaptive"))
        self.assertEqual(corr.freshness_window_min([40.0] * 12), (45.0, "adaptive"))
        one = [{"ts_utc": "2026-07-15T19:15:00+00:00", "cur_f": 74.0,
                "valid_local": "2026-07-15T11:25:00-0700", "secondaries": dict(SEC_A)}]
        # Fallback basis (5 intervals -> 45-min window): a 50-min-old stamp is STALE.
        r = self._eval(cur_f=74.0, valid_local="2026-07-15T11:25:00-0700",
                       reads=one, recorded_f=72.0, inter_obs=[20.0] * 5)
        self.assertEqual(r.freshness_basis, "fallback")
        self.assertEqual(r.freshness_window_min, 45.0)
        self.assertFalse(r.fresh)
        self.assertEqual(r.provenance, prov.UNCORROBORATED_NOWCAST)
        # 30-min-old stamp under the same fallback window is FRESH -> converging banks.
        one2 = [dict(one[0], valid_local="2026-07-15T11:45:00-0700")]
        r = self._eval(cur_f=74.0, valid_local="2026-07-15T11:45:00-0700",
                       reads=one2, recorded_f=72.0, inter_obs=[20.0] * 5,
                       fused_with=_f2c(74.0), fused_without=_f2c(72.0))
        self.assertTrue(r.fresh)
        self.assertEqual(r.provenance, prov.CORROBORATED_NOWCAST)
        # Adaptive basis (12×20min -> 30-min window): 35-min-old -> stale; 25 -> fresh.
        one3 = [dict(one[0], valid_local="2026-07-15T11:40:00-0700")]
        r = self._eval(cur_f=74.0, valid_local="2026-07-15T11:40:00-0700",
                       reads=one3, recorded_f=72.0, inter_obs=[20.0] * 12)
        self.assertEqual(r.freshness_basis, "adaptive")
        self.assertEqual(r.freshness_window_min, 30.0)
        self.assertFalse(r.fresh)
        one4 = [dict(one[0], valid_local="2026-07-15T11:50:00-0700")]
        r = self._eval(cur_f=74.0, valid_local="2026-07-15T11:50:00-0700",
                       reads=one4, recorded_f=72.0, inter_obs=[20.0] * 12,
                       fused_with=_f2c(74.0), fused_without=_f2c(72.0))
        self.assertTrue(r.fresh)
        self.assertEqual(r.provenance, prov.CORROBORATED_NOWCAST)

    def test_fail_closed_on_corrupt_or_missing_guard_state(self):
        # Fail-closed EVERYWHERE on state fault: an unprovable lead is stripped,
        # never banked — and evaluate never raises. Gap 3°F throughout so only a
        # provable sustained could ever corroborate.
        one = [{"ts_utc": "2026-07-15T19:15:00+00:00", "cur_f": 86.0,
                "valid_local": "2026-07-15T12:14:00-0700", "secondaries": dict(SEC_A)}]
        # (a) unparseable obs stamp -> freshness unprovable.
        r = self._eval(cur_f=86.0, valid_local="not-a-timestamp", reads=one,
                       recorded_f=83.0)
        self.assertEqual(r.provenance, prov.UNCORROBORATED_NOWCAST)
        self.assertEqual(r.served_running_max_c, 28.3333)
        # (b) unknown city -> no frozen config -> fail-closed.
        r = self._eval(cur_f=86.0, valid_local="2026-07-15T12:14:00-0700",
                       reads=one, recorded_f=83.0, icao="XXXX")
        self.assertEqual(r.provenance, prov.UNCORROBORATED_NOWCAST)
        self.assertEqual(r.served_running_max_c, 28.3333)
        # (c) corrupt ObsLog (garbage lines) -> no corroborating sequence -> the
        # lead cannot bank (the current read itself is still judged fresh).
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "corrupt.jsonl")
            with open(p, "w") as f:
                f.write("{not json\nnull\n[1,2]\n")
            self.assertEqual(obslog.load_reads("ksfo", "2026-07-15", path=p), [])
            self.assertEqual(obslog.load_reads("ksfo", "2026-07-15",
                                               path=os.path.join(tmp, "missing.jsonl")), [])
        r = self._eval(cur_f=86.0, valid_local="2026-07-15T12:14:00-0700",
                       reads=[], recorded_f=83.0, inter_obs=[20.0] * 12)
        self.assertFalse(r.sustained)
        self.assertEqual(r.provenance, prov.UNCORROBORATED_NOWCAST)
        self.assertEqual(r.served_running_max_c, 28.3333)
        # (d) unprovable clock (naive datetime) -> fail-closed.
        r = self._eval(cur_f=86.0, valid_local="2026-07-15T12:14:00-0700",
                       reads=one, recorded_f=83.0,
                       now=dt.datetime(2026, 7, 15, 12, 15))
        self.assertEqual(r.provenance, prov.UNCORROBORATED_NOWCAST)
        self.assertEqual(r.served_running_max_c, 28.3333)
        # (e) no cur_f at all -> nothing to judge; the pre-guard fusion passes
        # through untouched (provenance RECORDED, served = fused input).
        r = self._eval(cur_f=None, valid_local=None, reads=[], recorded_f=83.0)
        self.assertEqual(r.provenance, prov.RECORDED)
        self.assertEqual(r.served_running_max_c, 30.0)


if __name__ == "__main__":
    unittest.main()
