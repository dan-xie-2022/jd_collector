# LinkedIn Job Collector — Claude Code Workflow

## First-Time Setup (IMPORTANT)

When a user first opens this project, check if setup is complete:

1. Check if `.env` exists — if not, guide setup
2. Check if `RESUME_SUMMARY` in `collect_jobs.py` still contains "Your Name" — if so, guide setup

### Setup Flow

If setup is needed, walk the user through these steps one by one:

**Step 1 — Environment file**

> "Welcome! Let's set up your job collector. First, I need your LinkedIn credentials. These are stored locally in `.env` and never uploaded anywhere."

Ask for:
- LinkedIn email
- LinkedIn password
- (Optional) HTTP proxy address, if they're in a region where LinkedIn is blocked (e.g., China)
- (Optional) Anthropic API key, if they want automated scoring

Then create `.env` from `.env.example` with their values.

**Step 2 — Resume**

> "Now tell me about yourself so I can score jobs for you. Answer as much or as little as you want:"

Ask one by one:
- Name
- Current role and company
- Education
- Languages
- Key skills
- Work experience (brief bullet points)
- Target job titles
- Target industries
- Preferred company type
- Location and work mode preference (remote/hybrid/onsite)

Then update `RESUME_SUMMARY` in `collect_jobs.py` with their answers.

**Step 3 — Search terms**

> "What kind of roles are you looking for? Give me a few job titles and I'll set up the search terms."

Based on their answer, update `_SEARCH_TERMS_DEFAULT` or `_SEARCH_TERMS_CS` in `collect_jobs.py`, and set `MODE` accordingly.

**Step 4 — Location**

> "What city are you targeting? I'll configure the location filter."

Update `SEARCH_LOCATION` in `collect_jobs.py`. If the city is in `GEO_IDS`, it's already supported. If not, look up the LinkedIn geoId and add it.

**Step 5 — Confirm and run**

> "All set! Here's your config: [summary]. Ready to run your first search?"

If yes, run `python3 collect_jobs.py`.

---

## Project Overview

A Selenium-based LinkedIn job scraper. `collect_jobs.py` searches for jobs, fetches JDs, filters by location/industry/title, and outputs Excel files. **Scoring and application tracking are done by Claude Code in conversation — no external API calls needed.**

## Key Files

- `collect_jobs.py` — Main scraper, outputs `job_results_YYYYMMDD_HHMM.xlsx`
- `backfill_jd.py` — Backfill missing JDs in existing Excel files
- `applications.json` — Application tracking (maintained by Claude Code)
- `seen_jobs.json` — Seen job IDs (maintained by script, 30-day expiry)
- `.env` — Credentials (local only, gitignored)

## Scoring Criteria

Read `RESUME_SUMMARY` in `collect_jobs.py` for the user's profile. Score each job 1-10:

- **8-10**: Perfect match — target role + target industry + right seniority + correct location
- **6-7**: Strong match — most criteria met, minor gaps
- **4-5**: Partial match — some skill overlap but wrong industry/seniority/role type
- **1-3**: Poor match — fundamentally different role/industry/skill requirements
- Senior roles beyond the candidate's experience level: cap at 5
- Unrelated industries: cap at 3

## Workflow

### 1. After Collection

When the user runs `collect_jobs.py` or collection completes:

1. Find the most recent `job_results_*.xlsx` file
2. Read all job data (title, company, location, JD, URL)
3. Score each job with a JD (per criteria above)
4. Display results sorted by score: Score | Title | Company | One-line reason
5. Open URLs for jobs scoring 6+ using `open <url>` (macOS) or `xdg-open` (Linux)

### 2. Ask About Applications

After the user has browsed the links, ask:

> "Which of these did you apply to? Just tell me the numbers or company names. The rest will be marked as skipped — feel free to share why or not."

Update `applications.json`:
- Applied → status: "已投递", applied_date: today
- Skipped → status: "放弃", skip_reason: user's reason (optional)

### 3. Progress Tracking

The user can ask anytime:

- **"Show progress" / "查看进度"** — Read applications.json, summarize by status
- **"[Company] passed first interview"** — Fuzzy match company, update status
- **"What did I apply to today?"** — Filter by date
- **"Which ones haven't replied?"** — Filter "已投递" status older than 1 week

**Status flow**: 待投递 → 已投递 → HR联系 → 一面 → 二面 → 终面 → offer / 挂了 / 放弃

### 4. applications.json Structure

```json
[
  {
    "job_id": "4364083047",
    "title": "Solutions Engineer",
    "company": "Example Corp",
    "url": "https://www.linkedin.com/jobs/view/4364083047/",
    "applied_date": "2026-04-09",
    "status": "已投递",
    "score": 8,
    "score_reason": "Target role at target company, perfect match",
    "notes": "",
    "skip_reason": null,
    "updated_at": "2026-04-09T15:00:00"
  }
]
```

## Notes

- Do not modify `collect_jobs.py` unless the user asks
- `applications.json` is the single source of truth for tracking
- Excel files are read-only outputs from the scraper
- The script may require a proxy to access LinkedIn depending on region
- Use `python3` to run scripts (or `python3.11` etc. depending on the user's system)
