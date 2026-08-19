"""Offline checks for the feed generator. Run with: python tests/test_generate.py"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import generate  # noqa: E402
from icalendar import Calendar  # noqa: E402

SAMPLE = Path(__file__).parent / "sample_upstream.ics"
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
WINDOW_START = NOW - timedelta(days=21)
WINDOW_END = NOW + timedelta(days=400)

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


print("\nics source")
matches = generate.fetch_ics(
    f"file://{SAMPLE}", WINDOW_START, WINDOW_END, strip_pattern="Calendar not up to date"
)
summaries = [m.summary for m in matches]
check("drops matches older than history_days", "AC Milan - Lecce (4-0)" not in summaries)
check("drops matches beyond future_days", "AC Milan - Napoli" not in summaries)
check("keeps a recent result", "AC Milan - Cremonese (2-1)" in summaries)
check("keeps upcoming fixtures", {"AC Milan - Inter", "Juventus - AC Milan"} <= set(summaries))
check("strips the upstream nag line", all(m.description is None for m in matches))
check("preserves upstream UIDs", any(m.uid == "future1@maak-agenda.nl" for m in matches))
check("kickoff times are timezone-aware UTC", all(m.start.tzinfo is not None for m in matches))


print("\ncalendar output")
ics = generate.build_calendar("AC Milan", matches, alarm_minutes=30)
cal = Calendar.from_ical(ics)
events = list(cal.walk("VEVENT"))
check("one VEVENT per match", len(events) == len(matches), f"{len(events)} vs {len(matches)}")
check("calendar name is set", str(cal.get("x-wr-calname")) == "AC Milan")
check("refresh interval present", cal.get("refresh-interval") is not None)

alarms = [a for e in events for a in e.walk("VALARM")]
check("every event has exactly one alarm", len(alarms) == len(events))
triggers = {a.get("trigger").dt for a in alarms}
check("alarm fires 30 minutes before kickoff", triggers == {timedelta(minutes=-30)}, triggers)
check("alarm action is DISPLAY", all(str(a.get("action")) == "DISPLAY" for a in alarms))

raw = ics.decode()
check("trigger is relative, not absolute", "TRIGGER:-PT30M" in raw, raw[:0])
check("kickoffs serialise as UTC", "DTSTART:20260823T163000Z" in raw)
check("events are sorted by kickoff", [e.get("dtstart").dt for e in events] == sorted(e.get("dtstart").dt for e in events))

first = events[0]
check("event has an end time", first.get("dtend") is not None)
check("alarm text names the match", "kicks off in 30 minutes" in str(alarms[0].get("description")))


print("\nalarm_minutes is configurable")
ics_15 = generate.build_calendar("AC Milan", matches, alarm_minutes=15)
check("15-minute alarm honoured", "TRIGGER:-PT15M" in ics_15.decode())
ics_off = generate.build_calendar("AC Milan", matches, alarm_minutes=0)
check("alarm_minutes=0 disables alarms", "BEGIN:VALARM" not in ics_off.decode())


print("\ndeterminism")
check("same input produces identical bytes", generate.build_calendar("AC Milan", matches, 30) == ics)


print("\ndeduplication")


def fake_match(summary, start, uid="x@example.com"):
    return generate.Match(uid=uid, start=start, end=start + timedelta(hours=2), summary=summary)


derby = next(m for m in matches if m.summary == "AC Milan - Inter")
variants = [
    ("identical entry", "AC Milan - Inter", derby.start),
    ("different separator and suffixes", "AC Milan vs FC Internazionale Milano", derby.start),
    ("kickoff drifted by 15 minutes", "AC Milan vs Inter", derby.start + timedelta(minutes=15)),
    ("kickoff moved by an hour", "AC Milan vs Inter", derby.start + timedelta(hours=1)),
]
for label, summary, start in variants:
    merged = generate.dedupe(matches + [fake_match(summary, start, "other@example.com")])
    check(f"collapses: {label}", len(merged) == len(matches), f"{len(merged)} vs {len(matches)}")

distinct = [
    ("a different fixture days later", "AC Milan vs Lazio", derby.start + timedelta(days=4)),
    ("a genuine rescheduling far away", "AC Milan vs Inter", derby.start + timedelta(hours=6)),
]
for label, summary, start in distinct:
    merged = generate.dedupe(matches + [fake_match(summary, start, "other@example.com")])
    check(f"keeps separate: {label}", len(merged) == len(matches) + 1, f"{len(merged)}")

same_slot_reserves = generate.dedupe(
    matches + [fake_match("Cremonese Primavera - Lecce Primavera", derby.start, "y@example.com")]
)
check("does not merge unrelated teams at the same kickoff", len(same_slot_reserves) == len(matches) + 1)

check("first source wins", generate.dedupe([derby, fake_match("AC Milan vs Inter", derby.start)])[0] is derby)


print("\nfootball-data source")
FAKE = {
    "matches": [
        {
            "id": 12345,
            "utcDate": "2026-09-14T18:45:00Z",
            "status": "TIMED",
            "matchday": 3,
            "stage": "REGULAR_SEASON",
            "venue": "San Siro",
            "competition": {"name": "Serie A", "code": "SA"},
            "homeTeam": {"name": "AC Milan"},
            "awayTeam": {"name": "Bologna FC 1909"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
        {
            "id": 12346,
            "utcDate": "2026-09-20T16:00:00Z",
            "status": "FINISHED",
            "matchday": 4,
            "stage": "REGULAR_SEASON",
            "venue": "Stadio Olimpico",
            "competition": {"name": "Serie A", "code": "SA"},
            "homeTeam": {"name": "AS Roma"},
            "awayTeam": {"name": "AC Milan"},
            "score": {"fullTime": {"home": 1, "away": 2}},
        },
        {
            "id": 12347,
            "utcDate": "2026-09-24T19:00:00Z",
            "status": "CANCELLED",
            "competition": {"name": "Coppa Italia", "code": "CIT"},
            "homeTeam": {"name": "AC Milan"},
            "awayTeam": {"name": "Lecce"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
        {
            "id": 12348,
            "utcDate": "2026-10-01T19:00:00Z",
            "status": "SCHEDULED",
            "stage": "LEAGUE_STAGE",
            "competition": {"name": "UEFA Champions League", "code": "CL"},
            "homeTeam": {"name": "AC Milan"},
            "awayTeam": {"name": "Real Madrid CF"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
    ]
}


class FakeResponse:
    status_code = 200
    headers = {}

    def json(self):
        return FAKE

    def raise_for_status(self):
        pass


with mock.patch.object(generate.requests, "get", return_value=FakeResponse()) as fake_get:
    api_matches = generate.fetch_football_data(98, "token", WINDOW_START, WINDOW_END)
    call = fake_get.call_args
    check("token is sent as X-Auth-Token", call.kwargs["headers"]["X-Auth-Token"] == "token")
    check("date range is passed as filters", set(call.kwargs["params"]) == {"dateFrom", "dateTo"})

check("cancelled matches are skipped", len(api_matches) == 3, len(api_matches))
check("scheduled match summary", api_matches[0].summary == "AC Milan vs Bologna FC 1909")
check("finished match shows the score", api_matches[1].summary == "AS Roma vs AC Milan (1-2)")
check("venue becomes LOCATION", api_matches[0].location == "San Siro")
check("competition is recorded", api_matches[0].competition == "Serie A")
check("description carries matchday", "Matchday 3" in api_matches[0].description)
check("knockout stage is labelled", "League Stage" in api_matches[2].description)
check("UID is stable across runs", api_matches[0].uid == "fd-12345@soccer-calendar")
check("end time defaults to 2h after kickoff", api_matches[0].end - api_matches[0].start == timedelta(hours=2))

with mock.patch.object(generate.requests, "get", return_value=FakeResponse()):
    only_cl = generate.fetch_football_data(98, "token", WINDOW_START, WINDOW_END, competitions=["CL"])
check("competition filter works", [m.competition for m in only_cl] == ["UEFA Champions League"])

api_ics = generate.build_calendar("AC Milan", api_matches, 30).decode()
check("api events carry CATEGORIES", "CATEGORIES:Serie A" in api_ics)
check("api events carry LOCATION", "LOCATION:San Siro" in api_ics)


print("\nrate limiting")


class RateLimited(FakeResponse):
    status_code = 429


with mock.patch.object(generate.requests, "get", return_value=RateLimited()):
    try:
        generate.fetch_football_data(98, "token", WINDOW_START, WINDOW_END)
        check("429 raises a helpful error", False, "no exception raised")
    except RuntimeError as exc:
        check("429 raises a helpful error", "rate limit" in str(exc).lower())


print("\nresilience")
broken = {"name": "Broken", "sources": [{"type": "ics", "url": "file:///nonexistent.ics"}]}
found, errors = generate.collect(broken, "", WINDOW_START, WINDOW_END)
check("a dead source is reported, not fatal", found == [] and len(errors) == 1)

no_token = {"name": "NoToken", "sources": [{"type": "football-data", "team_id": 98}]}
found, errors = generate.collect(no_token, "", WINDOW_START, WINDOW_END)
check("missing token gives a clear message", any("FOOTBALL_DATA_TOKEN" in e for e in errors))


print("\nwrite_if_changed")
tmp = Path(__file__).parent / "_tmp_out.ics"
try:
    check("first write happens", generate.write_if_changed(tmp, b"a") is True)
    check("identical rewrite is skipped", generate.write_if_changed(tmp, b"a") is False)
    check("changed content is written", generate.write_if_changed(tmp, b"b") is True)
finally:
    tmp.unlink(missing_ok=True)


print("\nconfig file")
config = json.loads((Path(__file__).parent.parent / "config.json").read_text())
check("alarm_minutes is 30", config["alarm_minutes"] == 30)
check("at least one team configured", len(config["teams"]) >= 1)
for team in config["teams"]:
    check(f"{team['name']} has sources", bool(team.get("sources")))


print()
if failures:
    print(f"{len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All checks passed.")
