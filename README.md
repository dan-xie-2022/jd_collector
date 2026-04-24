# LinkedIn Job Collector

A Selenium-based LinkedIn job scraper with a Streamlit web UI, DeepSeek-powered job scoring, SQLite tracking, and daily email notifications. Designed to work with [Claude Code](https://claude.ai/claude-code) for AI-assisted application management.

## What It Does

- Searches LinkedIn for jobs across multiple search terms
- Fetches full job descriptions via logged-in Selenium browser
- Filters by location, industry, and title keywords
- Deduplicates results (by job ID and title+company)
- Scores each job 1–10 using DeepSeek API against your resume
- Outputs sorted Excel spreadsheets with color coding
- Stores all jobs and application status in SQLite (`jobs.db`)
- Sends email digest of high-scoring jobs after each run
- Tracks seen jobs with 30-day expiry to avoid re-processing

## Features

- **Two search rounds**: Primary location + country-wide remote jobs
- **Smart location filtering**: Keep target city + remote, skip other cities
- **GeoId-based search**: Precise LinkedIn location targeting
- **Industry/title pre-filters**: Auto-skip irrelevant roles
- **Two search modes**: `default` (customer-facing roles) and `cs` (developer roles)
- **Incremental Excel saves**: No data loss if interrupted during scoring
- **Streamlit web UI**: Browse jobs, manage applications, configure searches (`app.py`)
- **Scheduled daily runs**: `run_daily.py` invoked by launchd/cron, with email notification
- **Claude Code integration**: `CLAUDE.md` defines a complete AI-assisted workflow for scoring, analyzing, and tracking applications

## Project Structure

```
collect_jobs.py     # Main scraper → outputs Excel + scores jobs
backfill_jd.py      # Backfill missing JDs in existing Excel files
score_jobs.py       # Standalone scoring script (DeepSeek API)
app.py              # Streamlit web app
db.py               # SQLite interface (jobs.db)
notifier.py         # Email digest after each run
run_daily.py        # Entry point for scheduled daily runs
webapp_config.json  # Search config + resume (editable via UI)
```

## Setup

### Prerequisites

- Python 3.10+
- Chrome + ChromeDriver
- A LinkedIn account
- DeepSeek API key ([platform.deepseek.com](https://platform.deepseek.com))
- Gmail App Password (for email notifications)

### Install

```bash
pip install selenium pandas openpyxl python-dotenv openai streamlit
```

### Configure

```bash
cp .env.example .env
# Fill in your credentials
```

Edit `collect_jobs.py` to customize:
- `MODE` — `"default"` or `"cs"`
- `SEARCH_TERMS` — Keywords to search
- `RESUME_SUMMARY` — Your resume (used for DeepSeek scoring)
- `SEARCH_LOCATION` — Target city
- `DATE_FILTER` — `"Past 24 hours"`, `"Past week"`, or `"Past month"`
- `MAX_JOBS_PER_SEARCH` — Max results per search term
- `ENABLE_SCORING` — `True` to score via DeepSeek, `False` to skip

Or configure everything visually via the Streamlit app (see below).

If you're in a region where LinkedIn is blocked, set `HTTP_PROXY` in `.env`.

## Usage

### Collect jobs

```bash
python collect_jobs.py
```

### Backfill missing job descriptions

```bash
python backfill_jd.py                    # uses most recent Excel
python backfill_jd.py job_results.xlsx   # specify a file
```

### Score jobs manually

```bash
python score_jobs.py                     # scores most recent Excel
python score_jobs.py job_results.xlsx    # specify a file
```

### Web UI

```bash
streamlit run app.py
```

Browse collected jobs, track application status, and update search settings from the browser.

### Scheduled daily runs

```bash
python run_daily.py
```

Runs the collector, then sends an email digest of high-scoring new jobs. Wire this to launchd (macOS) or cron to run automatically each morning.

### With Claude Code

The included `CLAUDE.md` instructs Claude Code to:

1. Read the Excel output and score/analyze each job in conversation
2. Open high-scoring job URLs for you to review
3. Ask which jobs you applied to
4. Track application progress in `applications.json`
5. Update status anytime via natural language ("Company X passed first interview")

Just run the collector, then chat with Claude Code about the results.

## Why No Anti-Scraping Code?

This tool uses Selenium with a real Chrome browser and your own LinkedIn credentials — it's essentially automating what you'd do manually. It logs in as you, browses pages at human-like speed, and waits for content to load naturally. LinkedIn's anti-bot measures (CAPTCHAs, email verification) are handled by pausing for manual intervention. This is not a headless scraper hitting public endpoints at scale, so techniques like IP rotation, header spoofing, or rate limit evasion aren't needed or appropriate.

## Gotchas & Lessons Learned

These are real issues we hit while building and running this tool:

### LinkedIn Search Quirks

- **`location` parameter is unreliable.** Setting `location=Shanghai` still returns jobs in the US. The fix: use LinkedIn's `geoId` parameter (e.g., `geoId=102772228` for Shanghai). This project has a built-in `GEO_IDS` mapping.
- **"Remote" jobs pollute location searches.** LinkedIn treats "Remote" as a global location, so US remote jobs appear in China searches. Solution: we run two separate search rounds — one for the target city, one for country-wide remote only (`f_WT=2`).
- **Same job, different IDs.** LinkedIn sometimes assigns different `job_id`s to the same posting across search terms. We deduplicate by both `job_id` AND `title+company` to avoid processing duplicates.

### JD Fetching

- **`curl` can't get full job descriptions.** LinkedIn's public pages are heavily gated — unauthenticated requests return partial or empty content. Solution: use the already-logged-in Selenium browser to navigate to the job detail page.
- **JD content loads asynchronously.** Even with Selenium, the job description panel loads after the initial page render. Without a `WebDriverWait`, you'll get empty text. We wait up to 10 seconds for known JD CSS selectors to appear.
- **LinkedIn obfuscates CSS class names.** The selectors for job descriptions change periodically. We try multiple known selectors and fall back to extracting the longest text blocks from page source.

### Search Term Pitfalls

- **"Customer Engineer" means different things.** In the US/SaaS world, it's a customer-facing technical role (Google, JetBrains). In China, it typically means semiconductor equipment field engineer (Applied Materials, KLA). If you're targeting SaaS roles, use "Customer Success Engineer" instead.
- **Broad terms = noise.** Terms like "SaaS Customer Success" return tons of pure customer service (客服) roles. "Payment Integration" returns mostly hardware jobs. Be specific in your search terms.

### Data Management

- **`seen_jobs.json` grows forever** if you don't expire entries. We added timestamps to each entry and auto-expire after 30 days, so the file stays manageable and old jobs can resurface.
- **Searching a wide region (e.g., "China") pollutes `seen_jobs`** — jobs in other cities get marked as seen, so they'll be skipped even if they later become remote. Solution: either search your specific city, or don't write location-filtered jobs to `seen_jobs`.

### Environment

- **Python version matters.** Python 3.7 can't install modern `anthropic` or `tokenizers` packages. Use Python 3.10+.
- **Proxy is essential in some regions.** LinkedIn is blocked in mainland China. The script supports proxy via `HTTP_PROXY` environment variable. Make sure your proxy (Clash, V2Ray, etc.) is running before starting.
- **LinkedIn security checks.** After login, LinkedIn may present a CAPTCHA or email verification. The script detects this and pauses for manual intervention.

## License

MIT
