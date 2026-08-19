#!/usr/bin/env python3
"""
Build auto-updating iCalendar feeds for soccer teams, with a reminder
before every kickoff.

Two data sources are supported per team:

  "football-data"  Uses the football-data.org API (free tier covers Serie A,
                   Champions League, Premier League, La Liga, Bundesliga,
                   Ligue 1 and a few more). Gives competition names, venues
                   and reliable kickoff times. Needs a free API token in the
                   FOOTBALL_DATA_TOKEN environment variable.

  "ics"            Mirrors an existing public .ics feed and adds the alarm.
                   No API key, and it usually covers domestic cups too, but
                   you inherit whatever that feed decides to publish.

You can list several sources for one team; matches are de-duplicated.

Usage:
    python generate.py                      # build feeds into ./docs
    python generate.py --find-team milan    # look up football-data team ids
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from icalendar import Alarm, Calendar, Event

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
API_BASE = "https://api.football-data.org/v4"
USER_AGENT = "soccer-calendar/1.0 (+https://github.com/)"

# Competitions searched by --find-team. These are the ones on the free tier.
FREE_TIER_COMPETITIONS = ["SA", "PL", "PD", "BL1", "FL1", "CL", "DED", "PPL", "ELC", "BSA"]


# --------------------------------------------------------------------------
# Match model
# --------------------------------------------------------------------------


class Match:
    """One fixture, normalised across data sources."""

    def __init__(self, uid, start, end, summary, competition=None, location=None, description=None):
        self.uid = uid
        self.start = start  # tz-aware datetime
        self.end = end  # tz-aware datetime
        self.summary = summary
        self.competition = competition
        self.location = location
        self.description = description

    @property
    def tokens(self):
        """Distinctive words in the fixture name, ignoring club boilerplate."""
        words = re.findall(r"[a-z]{4,}", _slug(self.summary).replace("-", " "))
        return [w for w in words if w not in NOISE_WORDS]

    def is_same_match_as(self, other):
        """
        Two entries describe the same fixture.

        Called only within a single team's sources, where the strongest signal
        is the kickoff time: a team cannot play two matches within a couple of
        hours of each other. Sources often disagree by a few minutes (and
        occasionally by an hour when one hasn't picked up a rescheduling), so
        the window is generous, and we additionally require the names to share
        at least one distinctive word — which guards against a feed that
        bundles in a reserve or women's fixture at the same time.
        """
        if abs((self.start - other.start).total_seconds()) > 100 * 60:
            return False
        return any(_similar(a, b) for a in self.tokens for b in other.tokens)


# Words that appear in club names without identifying the club, so they must
# not count as evidence that two fixtures are the same.
NOISE_WORDS = {
    "club", "calcio", "futbol", "football", "sport", "sports", "sportiva",
    "sportif", "associazione", "association", "athletic", "atletico",
    "deportivo", "real", "city", "united", "town", "olympique", "olympic",
    "sporting", "borussia", "dynamo", "spartak", "verein", "team", "versus",
}


def _similar(a, b):
    """Loose word match so "inter" lines up with "internazionale"."""
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= 4 and longer.startswith(shorter)


def _slug(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --------------------------------------------------------------------------
# Source: football-data.org
# --------------------------------------------------------------------------


def fetch_football_data(team_id, token, window_start, window_end, competitions=None):
    url = f"{API_BASE}/teams/{team_id}/matches"
    params = {
        "dateFrom": window_start.strftime("%Y-%m-%d"),
        "dateTo": window_end.strftime("%Y-%m-%d"),
    }
    resp = requests.get(
        url,
        params=params,
        headers={"X-Auth-Token": token, "User-Agent": USER_AGENT},
        timeout=30,
    )
    if resp.status_code == 429:
        raise RuntimeError(
            "football-data.org rate limit hit (free tier allows 10 requests/minute). "
            "Re-run in a minute, or reduce the number of teams."
        )
    resp.raise_for_status()

    allowed = {c.upper() for c in competitions} if competitions else None
    matches = []

    for item in resp.json().get("matches", []):
        if item.get("status") in {"CANCELLED", "SUSPENDED"}:
            continue

        comp = (item.get("competition") or {}).get("name")
        comp_code = (item.get("competition") or {}).get("code")
        if allowed and not ({str(comp_code).upper(), _slug(comp).upper()} & allowed):
            continue

        start = _parse_iso(item["utcDate"])
        home = (item.get("homeTeam") or {}).get("name") or "TBD"
        away = (item.get("awayTeam") or {}).get("name") or "TBD"
        summary = f"{home} vs {away}"

        score = item.get("score") or {}
        full = score.get("fullTime") or {}
        if item.get("status") == "FINISHED" and full.get("home") is not None:
            summary += f" ({full['home']}-{full['away']})"

        notes = [comp] if comp else []
        if item.get("matchday"):
            notes.append(f"Matchday {item['matchday']}")
        if item.get("stage") and item["stage"] != "REGULAR_SEASON":
            notes.append(item["stage"].replace("_", " ").title())

        matches.append(
            Match(
                uid=f"fd-{item['id']}@soccer-calendar",
                start=start,
                # No official end time in the feed; 2h covers 90 minutes plus
                # half-time and stoppage.
                end=start + timedelta(hours=2),
                summary=summary,
                competition=comp,
                location=item.get("venue"),
                description=" · ".join(notes) or None,
            )
        )

    return matches


def _parse_iso(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def find_team(query, token):
    """Print football-data.org team ids matching a name."""
    needle = _slug(query)
    hits = []
    for code in FREE_TIER_COMPETITIONS:
        try:
            resp = requests.get(
                f"{API_BASE}/competitions/{code}/teams",
                headers={"X-Auth-Token": token, "User-Agent": USER_AGENT},
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            for team in resp.json().get("teams", []):
                names = [team.get("name", ""), team.get("shortName") or "", team.get("tla") or ""]
                if any(needle in _slug(n) for n in names):
                    hits.append((team["id"], team.get("name"), code))
        except requests.RequestException:
            continue

    if not hits:
        print(f'No team matching "{query}" found in the free-tier competitions.')
        return 1

    print(f'Teams matching "{query}":\n')
    for team_id, name, code in sorted(set(hits)):
        print(f"  id {team_id:<6} {name}   (found in {code})")
    print('\nAdd one to config.json as {"type": "football-data", "team_id": <id>}')
    return 0


# --------------------------------------------------------------------------
# Source: mirror an existing .ics feed
# --------------------------------------------------------------------------


def fetch_ics(url, window_start, window_end, strip_pattern=None):
    # webcal:// is just https:// with a different scheme, so accept it too.
    # A plain path or file:// URL is handy for offline testing.
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]

    if url.startswith("file://"):
        raw = Path(url[len("file://") :]).read_bytes()
    elif "://" not in url:
        raw = (ROOT / url).read_bytes()
    else:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        resp.raise_for_status()
        raw = resp.content

    cal = Calendar.from_ical(raw)

    matches = []
    for component in cal.walk("VEVENT"):
        start = component.get("dtstart")
        if start is None:
            continue
        start = _as_utc(start.dt)
        if not (window_start <= start <= window_end):
            continue

        end_prop = component.get("dtend")
        end = _as_utc(end_prop.dt) if end_prop is not None else start + timedelta(hours=2)

        summary = str(component.get("summary") or "Match")
        description = str(component.get("description") or "") or None
        if description and strip_pattern and re.search(strip_pattern, description):
            description = None

        raw_uid = str(component.get("uid") or "")
        uid = raw_uid or f"ics-{hashlib.sha1(f'{start}{summary}'.encode()).hexdigest()[:16]}"

        matches.append(
            Match(
                uid=uid,
                start=start,
                end=end,
                summary=summary,
                location=str(component.get("location") or "") or None,
                description=description,
            )
        )

    return matches


def _as_utc(value):
    if not isinstance(value, datetime):  # all-day event: treat as midnight UTC
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Calendar building
# --------------------------------------------------------------------------


def build_calendar(name, matches, alarm_minutes, refresh_hours=6):
    cal = Calendar()
    cal.add("prodid", "-//soccer-calendar//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-caldesc", f"Fixtures with a {alarm_minutes}-minute reminder")
    # Both spellings: REFRESH-INTERVAL is the standard, X-PUBLISHED-TTL is
    # what older Outlook/Apple versions read.
    cal.add("refresh-interval;value=duration", f"PT{refresh_hours}H")
    cal.add("x-published-ttl", f"PT{refresh_hours}H")

    for match in sorted(matches, key=lambda m: m.start):
        event = Event()
        event.add("uid", match.uid)
        event.add("dtstart", match.start)
        event.add("dtend", match.end)
        # Deterministic DTSTAMP: keeps the output byte-identical between runs
        # when nothing about the fixture list has changed.
        event.add("dtstamp", match.start)
        event.add("summary", match.summary)
        event.add("status", "CONFIRMED")
        event.add("transp", "TRANSPARENT")
        if match.location:
            event.add("location", match.location)
        if match.description:
            event.add("description", match.description)
        if match.competition:
            event.add("categories", [match.competition])

        if alarm_minutes and alarm_minutes > 0:
            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            alarm.add("description", f"{match.summary} kicks off in {alarm_minutes} minutes")
            alarm.add("trigger", timedelta(minutes=-alarm_minutes))
            event.add_component(alarm)

        cal.add_component(event)

    return cal.to_ical()


def build_index(entries, alarm_minutes):
    """A tiny landing page with one-tap subscribe links.

    The webcal:// URLs are built in the browser from the current address, so
    the page works whatever your GitHub username or repository name is.
    """
    rows = "\n".join(
        f'      <li><a class="feed" data-file="{file}" href="{file}">{label}</a>'
        f'<a class="raw" href="{file}">download</a></li>'
        for label, file in entries
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Soccer calendars</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 34rem; margin: 3rem auto; padding: 0 1.25rem; }}
  h1 {{ font-size: 1.4rem; margin-bottom: .25rem; }}
  p.sub {{ opacity: .7; margin-top: 0; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ display: flex; align-items: baseline; gap: .75rem;
        padding: .8rem 0; border-bottom: 1px solid rgba(128,128,128,.25); }}
  a.feed {{ font-weight: 600; text-decoration: none; flex: 1; }}
  a.feed:hover {{ text-decoration: underline; }}
  a.raw {{ font-size: .8rem; opacity: .6; }}
  footer {{ margin-top: 2rem; font-size: .85rem; opacity: .7; }}
</style>
</head>
<body>
  <h1>Soccer calendars</h1>
  <p class="sub">Tap a calendar to subscribe. Every match carries a
     {alarm_minutes}-minute reminder.</p>
  <ul>
{rows}
  </ul>
  <footer>
    On iPhone, choose <strong>Subscribe</strong> and leave
    &ldquo;Remove Alarms&rdquo; switched <strong>off</strong>, otherwise iOS
    strips the reminders.
  </footer>
<script>
  var base = location.href.replace(/[^/]*$/, '');
  document.querySelectorAll('a.feed').forEach(function (link) {{
    link.href = 'webcal://' + base.replace(/^https?:\\/\\//, '') + link.dataset.file;
  }});
</script>
</body>
</html>
"""


def write_if_changed(path, content):
    """Avoid rewriting identical files so git history stays meaningful."""
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit(f"Missing config file: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text())


def collect(team, token, window_start, window_end):
    matches, errors = [], []
    for source in team.get("sources", []):
        kind = source.get("type")
        try:
            if kind == "football-data":
                if not token:
                    raise RuntimeError(
                        "FOOTBALL_DATA_TOKEN is not set. Get a free key at "
                        "https://www.football-data.org/client/register"
                    )
                matches += fetch_football_data(
                    source["team_id"],
                    token,
                    window_start,
                    window_end,
                    source.get("competitions"),
                )
            elif kind == "ics":
                matches += fetch_ics(
                    source["url"],
                    window_start,
                    window_end,
                    strip_pattern=source.get("strip_description_matching"),
                )
            else:
                raise RuntimeError(f"Unknown source type: {kind!r}")
        except Exception as exc:  # keep other teams/sources working
            errors.append(f"{kind}: {exc}")

    return dedupe(matches), errors


def dedupe(matches):
    """Collapse the same fixture arriving from more than one source.

    The first source listed for a team wins, so put the one with the better
    metadata first in config.json.
    """
    unique = []
    for match in matches:  # config order, so the preferred source is seen first
        if not any(match.is_same_match_as(kept) for kept in unique):
            unique.append(match)
    return unique


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--find-team", metavar="NAME", help="look up football-data.org team ids and exit")
    parser.add_argument("--out", default=None, help="output directory (default: from config)")
    args = parser.parse_args()

    token = os.environ.get("FOOTBALL_DATA_TOKEN", "").strip()

    if args.find_team:
        if not token:
            sys.exit("Set FOOTBALL_DATA_TOKEN first (free key: https://www.football-data.org/client/register)")
        return find_team(args.find_team, token)

    config = load_config()
    alarm_minutes = int(config.get("alarm_minutes", 30))
    history_days = int(config.get("history_days", 21))
    future_days = int(config.get("future_days", 400))
    out_dir = Path(args.out or config.get("output_dir", "docs"))
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=history_days)
    window_end = now + timedelta(days=future_days)

    everything, problems, written, index_entries = [], [], [], []

    for team in config.get("teams", []):
        name = team["name"]
        slug = team.get("slug") or _slug(name)
        matches, errors = collect(team, token, window_start, window_end)
        problems += [f"{name} → {e}" for e in errors]

        if not matches:
            print(f"  {name}: no matches found — leaving existing file untouched")
            continue

        content = build_calendar(name, matches, alarm_minutes)
        changed = write_if_changed(out_dir / f"{slug}.ics", content)
        written.append(f"  {name}: {len(matches)} matches → {slug}.ics{'' if changed else ' (unchanged)'}")
        index_entries.append((name, f"{slug}.ics"))
        everything += matches

    combined_name = config.get("combined_name", "Soccer")
    if everything and config.get("write_combined", True):
        # Two configured teams playing each other would otherwise appear twice.
        unique = dedupe(sorted(everything, key=lambda m: m.start))
        content = build_calendar(combined_name, unique, alarm_minutes)
        changed = write_if_changed(out_dir / "all.ics", content)
        written.append(f"  Combined: {len(unique)} matches → all.ics{'' if changed else ' (unchanged)'}")
        if len(index_entries) > 1:
            index_entries.append((f"{combined_name} — all teams", "all.ics"))

    if index_entries:
        write_if_changed(out_dir / "index.html", build_index(index_entries, alarm_minutes).encode())
        # Stops GitHub Pages from running the files through Jekyll.
        write_if_changed(out_dir / ".nojekyll", b"")

    print("\n".join(written) if written else "Nothing written.")

    if problems:
        print("\nProblems:", file=sys.stderr)
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)
        # Fail only if we produced nothing at all, so one broken source
        # doesn't wipe out a working calendar.
        if not written:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
