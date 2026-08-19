# Soccer calendar feeds

An auto-updating calendar of your team's fixtures, with a reminder 30 minutes
before every kickoff. Subscribe once on your iPhone and never touch it again.

No app, no App Store, no Apple developer account. A GitHub Action rebuilds the
feed twice a day for free; iOS refreshes it in the background and fires the
alarms.

Out of the box it's set up for AC Milan. Adding more teams is a few lines in
`config.json`.

---

## Setup (about 10 minutes)

### 1. Put this in a GitHub repository

```bash
cd soccer-calendar
git init -b main
git add .
git commit -m "Soccer calendar feeds"
gh repo create soccer-calendar --public --source=. --push
```

No `gh` CLI? Create an empty repo on github.com, then:

```bash
git remote add origin https://github.com/YOUR-USERNAME/soccer-calendar.git
git push -u origin main
```

The repository needs to be **public** — iOS has to be able to read the `.ics`
file without logging in. Nothing private is in here.

### 2. Turn on GitHub Pages

In the repo: **Settings → Pages**. Under "Build and deployment" set

- Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs**

Save. After a minute your feeds live at:

```
https://YOUR-USERNAME.github.io/soccer-calendar/
```

### 3. Let the Action write to the repo

**Settings → Actions → General → Workflow permissions** → select
**Read and write permissions**. Save. (Without this the job can't commit the
regenerated feed.)

### 4. Run it once

**Actions → Update calendars → Run workflow**. It runs the tests, builds the
feeds, and commits them into `docs/`.

### 5. Subscribe on your iPhone

Open `https://YOUR-USERNAME.github.io/soccer-calendar/` in Safari on the phone
and tap your team. iOS will offer to subscribe.

> **Important:** when iOS asks, leave **"Remove Alarms"** switched **off**.
> Turn it on and you get the fixtures but none of the reminders — the alarms
> live inside the feed, and a subscribed calendar is read-only, so you can't
> add them back afterwards.

Then in **Settings → Calendar → Accounts → Subscribed Calendars → (your feed)**
set **Refresh** to **Every hour** or **Every day** so kickoff changes reach you.

That's it. The calendar shows up on your Mac and Apple Watch too, via iCloud.

---

## Adding more teams

Edit `config.json`, commit, push. The Action does the rest.

The default source mirrors a free public feed from fixtur.es, which covers
Serie A plus the Champions League, Europa League, Conference League and Coppa
Italia. Their URLs follow the team's name:

```json
{
  "name": "Arsenal",
  "slug": "arsenal",
  "sources": [
    { "type": "ics",
      "url": "https://ics.fixtur.es/v2/arsenal.ics",
      "strip_description_matching": "Calendar not up to date" }
  ]
}
```

Check the team exists there first — visit `https://fixtur.es/en/team/arsenal`
and confirm the feed URL it offers.

### Or use the football-data.org API

More dependable long-term than mirroring someone else's feed, and it gives you
competition names, matchdays and stadium names in the event. It needs a free
key.

1. Register at <https://www.football-data.org/client/register> — free, instant.
2. In your repo: **Settings → Secrets and variables → Actions → New repository
   secret**, named `FOOTBALL_DATA_TOKEN`.
3. Find your team's numeric id:

   ```bash
   export FOOTBALL_DATA_TOKEN=your-key-here
   python generate.py --find-team milan
   ```

4. Add it as a source:

   ```json
   {
     "name": "AC Milan",
     "slug": "ac-milan",
     "sources": [
       { "type": "football-data", "team_id": 98 },
       { "type": "ics", "url": "https://ics.fixtur.es/v2/ac-milan.ics",
         "strip_description_matching": "Calendar not up to date" }
     ]
   }
   ```

Listing both is the belt-and-braces setup: the API is authoritative, the ICS
feed fills in competitions the free tier doesn't carry, and matches appearing
in both are de-duplicated (the first source listed wins).

The free tier covers Serie A, the Champions League, Premier League, La Liga,
Bundesliga, Ligue 1, Eredivisie, Primeira Liga, the Championship, Brasileirão,
the World Cup and the Euros — but **not** domestic cups like the Coppa Italia,
and it allows 10 requests per minute (one per team per run, so you have room
for plenty of teams).

---

## Settings

All in `config.json`:

| Key              | Default   | What it does                                        |
| ---------------- | --------- | --------------------------------------------------- |
| `alarm_minutes`  | `30`      | Minutes before kickoff. `0` disables reminders.      |
| `history_days`   | `21`      | How far back to keep played matches.                |
| `future_days`    | `400`     | How far ahead to include fixtures.                  |
| `output_dir`     | `docs`    | Where the `.ics` files are written.                 |
| `write_combined` | `true`    | Also write `all.ics` with every team in one feed.   |
| `combined_name`  | `Soccer`  | Calendar name for `all.ics`.                        |

Change `alarm_minutes` and every event in the feed updates on the next run —
your phone picks it up on its next refresh.

---

## Running it locally

```bash
pip install -r requirements.txt
python generate.py          # writes docs/*.ics
python tests/test_generate.py
```

The test suite is fully offline (it uses `tests/sample_upstream.ics` and a
mocked API), so it's fast and won't burn API quota.

---

## Things worth knowing

**Fixtures move.** Serie A confirms kickoff times only a few weeks ahead, and
TV scheduling shifts them. That's exactly why this is a live feed rather than a
one-off export — but it means a match six months out is a placeholder date.

**GitHub pauses idle schedules.** If nobody pushes to the repo for 60 days,
GitHub disables the scheduled workflow and emails you a link to re-enable it.
Commits made by the Action itself don't always reset that clock. Re-enabling is
one click, and running the workflow by hand from the Actions tab also resets it.

**iOS decides when to refresh.** Even set to "Every hour", iOS batches
subscribed-calendar refreshes to save battery. Fine for fixtures; don't expect
it to track a kickoff that moved in the last twenty minutes.

**Alarms are per-feed, not per-event.** Because the calendar is read-only, you
can't tweak the reminder for one specific match on the phone. Change
`alarm_minutes` for all of them, or add that one match to your own calendar
manually.

**A broken source won't wipe your calendar.** If a fetch fails, the script logs
it and leaves the existing `.ics` in place rather than publishing an empty one.

---

## Layout

```
generate.py                          the whole thing
config.json                          teams and settings
docs/                                published feeds (GitHub Pages serves this)
tests/test_generate.py               offline checks
tests/sample_upstream.ics            fixture data for the tests
.github/workflows/update-calendars.yml   twice-daily rebuild
```
