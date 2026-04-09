'''
LinkedIn Job Collector
- Searches LinkedIn for jobs (no auto-apply)
- Scores each job against your resume using Claude
- Outputs a sorted Excel spreadsheet
'''

import time
import csv
import os
import re
import json
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import anthropic
import pandas as pd
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ─── CONFIG ───────────────────────────────────────────────────────────────────

LINKEDIN_EMAIL    = os.environ["LINKEDIN_EMAIL"]
LINKEDIN_PASSWORD = os.environ["LINKEDIN_PASSWORD"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

ENABLE_SCORING = False          # True = use Claude API to score jobs, False = skip scoring (no API cost)

# ─── MODE ────────────────────────────────────────────────────────────────────
# "default" = Solutions Engineer / TAM / Customer Success 等岗位
# "cs"      = Computer Science 相关开发岗位（Software Engineer, Backend, etc.）
MODE = "default"

# ─── SEARCH TERMS BY MODE ────────────────────────────────────────────────────
_SEARCH_TERMS_DEFAULT = [
    # Examples — customize these to your target roles
    "Solutions Engineer",
    "Technical Account Manager",
    "Customer Success Manager",
    "Technical Consultant",
    "Solution Consultant",
]

_SEARCH_TERMS_CS = [
    # Examples — customize these to your target roles
    "Software Engineer",
    "Backend Developer",
    "Data Analyst",
    "Python Developer",
    "DevOps Engineer",
]

SEARCH_TERMS = _SEARCH_TERMS_CS if MODE == "cs" else _SEARCH_TERMS_DEFAULT

# ─── JD RELEVANCE FILTER ─────────────────────────────────────────────────────
# Jobs whose title or JD matches these patterns are auto-excluded
EXCLUDE_INDUSTRIES = re.compile(
    r'pharma|medical|clinical|biotech|nursing|dental|implant|'
    r'semiconductor|NAND|wafer|fab\b|chip design|'
    r'civil engineer|construction|建筑|mechanical engineer|'
    r'chemical engineer|petroleum|oil\s*&?\s*gas|'
    r'food scientist|agronomist|veterinar',
    re.IGNORECASE
)
# Jobs whose title or JD contains these keywords get a relevance boost
_RELEVANT_KEYWORDS_DEFAULT = re.compile(
    r'API|integration|SaaS|platform|fintech|payment|checkout|'
    r'merchant|e-?commerce|cloud|software|digital|tech|IT|'
    r'implementation|onboarding|customer success|solutions engineer|'
    r'B2B|enterprise software|CRM|ERP',
    re.IGNORECASE
)
_RELEVANT_KEYWORDS_CS = re.compile(
    r'Python|SQL|pandas|data.?analysis|ETL|'
    r'database|MySQL|PostgreSQL|SQLite|'
    r'BI|Tableau|Power\s*BI|Excel|'
    r'automation|scripting|report|dashboard|'
    r'junior|entry.?level|graduate|'
    r'computer science',
    re.IGNORECASE
)
RELEVANT_KEYWORDS = _RELEVANT_KEYWORDS_CS if MODE == "cs" else _RELEVANT_KEYWORDS_DEFAULT

SEARCH_LOCATION = "Shanghai"
DATE_FILTER     = "Past 24 hours"      # "Past 24 hours", "Past week", "Past month"
MAX_JOBS_PER_SEARCH = 40        # max jobs to collect per search term

RESUME_SUMMARY = """
Name: Your Name
Current Role: Your current job title at Company, Start Date - Present
Education: Degree, University, Year
Languages: List your languages
Skills: List key technical and soft skills
Experience:
- Company A: Brief description of role and achievements
- Company B: Brief description of role and achievements
Target roles: List your target job titles
Target industry: List your target industries
Preferred company profile: Describe your ideal company type
Location: Your city, Country (work preferences)
"""

OUTPUT_FILE   = f"job_results_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
SEEN_IDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_jobs.json")

SEEN_EXPIRE_DAYS = 30  # seen jobs older than this are forgotten

def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE) as f:
            data = json.load(f)
        # Migrate from old list format to {id: timestamp} dict
        if isinstance(data, list):
            now = datetime.now().isoformat()
            return {jid: now for jid in data}
        # Expire old entries
        cutoff = datetime.now().timestamp() - SEEN_EXPIRE_DAYS * 86400
        return {jid: ts for jid, ts in data.items()
                if datetime.fromisoformat(ts).timestamp() > cutoff}
    return {}

def save_seen_ids(seen_ids):
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(seen_ids, f)

# ─── BROWSER SETUP ────────────────────────────────────────────────────────────

def create_driver():
    # Ensure ChromeDriver's local connections don't go through the proxy
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"

    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    proxy = os.environ.get("HTTP_PROXY", "")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")  # browser uses proxy to access LinkedIn
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    driver = webdriver.Chrome(options=options)
    return driver

def login(driver):
    print("Logging in to LinkedIn...")
    driver.get("https://www.linkedin.com/login")
    wait = WebDriverWait(driver, 30)
    wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(LINKEDIN_EMAIL)
    driver.find_element(By.ID, "password").send_keys(LINKEDIN_PASSWORD)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    # Wait until login completes (feed page or security check)
    time.sleep(5)
    if "checkpoint" in driver.current_url or "challenge" in driver.current_url:
        print("⚠ LinkedIn security check detected. Please complete it manually in the browser.")
        input("Press Enter after you've completed the verification...")
    print("Logged in.")

# ─── JOB SEARCH ───────────────────────────────────────────────────────────────

DATE_MAP = {
    "Past 24 hours": "r86400",
    "Past week":     "r604800",
    "Past month":    "r2592000",
}

# LinkedIn geoIds for location filtering
GEO_IDS = {
    "China":    "102890883",
    "Shanghai": "102772228",
    "Shenzhen": "101591017",
    "Beijing":  "101780494",
}

def build_search_url(search_term, location, date_filter, remote_only=False):
    from urllib.parse import quote
    term_enc = quote(search_term)
    loc_enc  = quote(location)
    date_param = DATE_MAP.get(date_filter, "r604800")
    geo_id = GEO_IDS.get(location, "")
    url = (
        f"https://www.linkedin.com/jobs/search/"
        f"?keywords={term_enc}&location={loc_enc}"
        f"&f_TPR={date_param}&sortBy=DD"
    )
    if geo_id:
        url += f"&geoId={geo_id}"
    if remote_only:
        url += "&f_WT=2"  # LinkedIn filter: Remote only
    return url

def get_job_cards(driver, max_jobs):
    jobs = []
    seen_ids = set()

    # wait for results to load
    time.sleep(3)

    for scroll_attempt in range(15):
        # Try multiple selectors for job cards
        cards = driver.find_elements(By.CSS_SELECTOR, "li[data-occludable-job-id]")
        if not cards:
            cards = driver.find_elements(By.CSS_SELECTOR, ".scaffold-layout__list-container li")
        if not cards:
            cards = driver.find_elements(By.CSS_SELECTOR, ".jobs-search-results__list li")

        for card in cards:
            try:
                job_id = card.get_attribute("data-occludable-job-id") or card.get_attribute("data-job-id") or card.get_attribute("data-entity-urn")
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                # Title and URL
                title_el = None
                for sel in ["a.job-card-list__title--link", "a.job-card-container__link", "a[href*='/jobs/view/']", ".job-card-list__title"]:
                    try:
                        title_el = card.find_element(By.CSS_SELECTOR, sel)
                        break
                    except NoSuchElementException:
                        continue
                if not title_el:
                    continue
                title = title_el.text.strip()
                url = title_el.get_attribute("href") or ""
                if "?" in url:
                    url = url.split("?")[0]

                # Company
                company = ""
                for sel in [".job-card-container__primary-description", ".artdeco-entity-lockup__subtitle span", ".job-card-container__company-name"]:
                    try:
                        company = card.find_element(By.CSS_SELECTOR, sel).text.strip()
                        if company:
                            break
                    except NoSuchElementException:
                        continue

                # Location
                location = ""
                for sel in [".job-card-container__metadata-item", ".artdeco-entity-lockup__caption li", ".job-card-container__metadata-wrapper li"]:
                    try:
                        location = card.find_element(By.CSS_SELECTOR, sel).text.strip()
                        if location:
                            break
                    except NoSuchElementException:
                        continue

                # Date
                date_text = ""
                for sel in ["time", ".job-card-container__listed-time", ".job-card-list__footer-wrapper time"]:
                    try:
                        el = card.find_element(By.CSS_SELECTOR, sel)
                        date_text = el.get_attribute("datetime") or el.text.strip()
                        if date_text:
                            break
                    except NoSuchElementException:
                        continue

                # Easy Apply badge
                easy_apply = False
                try:
                    badge_text = card.text.lower()
                    easy_apply = "easy apply" in badge_text or "快速申请" in badge_text
                except Exception:
                    pass

                if not title:
                    continue

                jobs.append({
                    "job_id":      job_id,
                    "title":       title,
                    "company":     company,
                    "location":    location,
                    "date_text":   date_text,
                    "url":         url,
                    "easy_apply":  easy_apply,
                    "description": "",
                })

                if len(jobs) >= max_jobs:
                    return jobs
            except Exception:
                continue

        # Scroll down in the results panel
        try:
            scroll_container = driver.find_element(By.CSS_SELECTOR, ".scaffold-layout__list-container, .jobs-search-results-list, .jobs-search__results-list")
            driver.execute_script("arguments[0].scrollTop += 800", scroll_container)
        except (NoSuchElementException, Exception):
            try:
                driver.execute_script("window.scrollBy(0, 800);")
            except Exception:
                pass
        time.sleep(1.5)

    return jobs

def get_job_description(driver, url):
    """Fetch JD using the logged-in Selenium browser for full content."""
    try:
        driver.get(url)

        # Wait for JD content to appear (up to 10 seconds)
        jd_selectors = [
            ".jobs-description__content",
            ".jobs-description-content__text",
            ".show-more-less-html__markup",
            ".jobs-box__html-content",
            "#job-details",
            "[class*='jobs-description']",
        ]
        combined_css = ", ".join(jd_selectors)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, combined_css))
            )
        except TimeoutException:
            pass  # fall through to fallback
        time.sleep(1)  # small extra wait for dynamic content

        # Try each selector
        for sel in jd_selectors:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                text = el.text.strip()
                if len(text) > 100:
                    return text[:3000]
            except NoSuchElementException:
                continue

        # Fallback: grab longest text blocks from page source
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.texts, self.current, self.skip = [], '', False
            def handle_starttag(self, tag, attrs):
                if tag in ('script', 'style', 'nav', 'header', 'footer'):
                    self.skip = True
            def handle_endtag(self, tag):
                if tag in ('script', 'style', 'nav', 'header', 'footer'):
                    self.skip = False
                t = self.current.strip()
                if len(t) > 150:
                    self.texts.append(t)
                self.current = ''
            def handle_data(self, data):
                if not self.skip:
                    self.current += data

        parser = TextExtractor()
        parser.feed(driver.page_source)
        parser.texts.sort(key=len, reverse=True)
        parts = [t for t in parser.texts[:4] if len(t) > 150]
        return "\n\n".join(parts)[:3000] if parts else ""
    except Exception:
        return ""

# ─── CLAUDE SCORING ───────────────────────────────────────────────────────────

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client

def score_job(title, company, description):
    prompt = f"""You are a strict career advisor. Score how well this job matches the candidate.

SCORING RULES (follow strictly):
- 8-10: Perfect match — target role + target industry + right seniority + China location
- 6-7: Strong match — most criteria met, minor gaps (e.g. adjacent role or industry)
- 4-5: Partial match — some skills overlap but wrong industry, seniority, or role type
- 1-3: Poor match — fundamentally different role, industry, or requires skills candidate lacks
- Jobs requiring significantly more experience than the candidate has: cap at 5
- Jobs in unrelated industries (automotive, aerospace, manufacturing, chemicals): cap at 3

CANDIDATE PROFILE:
{RESUME_SUMMARY}

JOB:
Title: {title}
Company: {company}
Description (excerpt):
{description[:2000]}

Respond in this exact format (nothing else):
SCORE: <1-10>
REASON: <one sentence why>
MATCH_TAGS: <comma-separated keywords that match, e.g. "bilingual, SaaS, technical support">
"""
    try:
        msg = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        text = msg.content[0].text.strip()
        score_match = re.search(r"SCORE:\s*(\d+)", text)
        reason_match = re.search(r"REASON:\s*(.+)", text)
        tags_match = re.search(r"MATCH_TAGS:\s*(.+)", text)
        score  = int(score_match.group(1)) if score_match else 5
        reason = reason_match.group(1).strip() if reason_match else ""
        tags   = tags_match.group(1).strip() if tags_match else ""
        return score, reason, tags
    except Exception as e:
        print(f"  Claude error: {e}")
        return 5, "Could not score", ""

# ─── PRE-FILTER ───────────────────────────────────────────────────────────────

# Keywords that indicate a job is NOT a good match (case-insensitive)
_TITLE_SKIP_DEFAULT = [
    "machine learning", "blockchain", "cloud engineer", "software engineer",
    "fullstack", "full stack", "full-stack", "ml engineer", "data engineer",
    "hardware", "clinical trial", "medical solution", "FAE", "intern",
    "analyst intern", "sales manager", "security sales", "ecommerce",
    "焊接", "模拟IC", "功率器件", "设计院", "汽车零部件",
    "director", "senior director", "vice president", "VP ", "head of",
    "principal", "staff engineer", "lead architect",
    # 纯客服岗
    "客服", "customer service", "call center", "电话客服", "在线客服",
    "客服专员", "客服前台", "淘宝客服", "天猫客服", "电商客服",
]
_TITLE_SKIP_CS = [
    "hardware", "clinical trial", "medical solution", "FAE",
    "sales manager", "security sales",
    "焊接", "模拟IC", "功率器件", "设计院", "汽车零部件",
    "director", "senior director", "vice president", "VP ", "head of",
    "principal", "lead architect", "staff engineer",
    "customer success", "account manager", "pre-sales",
    "senior software", "senior developer", "senior engineer",
    "frontend", "full stack", "fullstack", "full-stack",
    "Java", "Go ", "Rust", "C++", "React", "Node",
    "DevOps", "SRE", "infrastructure", "Kubernetes",
    "machine learning", "ml engineer", "blockchain",
]
TITLE_SKIP_KEYWORDS = _TITLE_SKIP_CS if MODE == "cs" else _TITLE_SKIP_DEFAULT

def has_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text))

_NON_CHINA_LOCATIONS = re.compile(
    r'United States|USA|\bUS\b|Remote\)$|Canada|United Kingdom|UK\b|'
    r'India|Germany|France|Australia|Japan|Singapore|Brazil|'
    r'NAMER|EMEA|APAC(?!.*China)|'
    r'\b[A-Z]{2},\s*United|\bNY\b|\bCA\b|\bTX\b|\bWA\b|\bMA\b|\bIL\b|'
    r'New York|San Francisco|Los Angeles|Seattle|Boston|Chicago|Austin|'
    r'London|Berlin|Tokyo|Bangalore|Hyderabad|Toronto|Vancouver',
    re.IGNORECASE
)

def is_acceptable_location(location):
    """
    Keep: Shanghai jobs, China Remote jobs, unknown location.
    Skip: non-China jobs, and non-Shanghai onsite/hybrid in China.
    """
    if not location:
        return True  # unknown, keep
    loc = location.strip()
    is_china = bool(re.search(
        r'China|中国|Shanghai|上海|Beijing|北京|Shenzhen|深圳|Guangzhou|广州|'
        r'Hangzhou|杭州|Chengdu|成都|Nanjing|南京|Suzhou|苏州|Wuhan|武汉|'
        r'Xi.an|西安|Dongguan|东莞',
        loc, re.IGNORECASE
    ))
    is_remote = bool(re.search(r'Remote', loc, re.IGNORECASE))
    is_shanghai = bool(re.search(r'Shanghai|上海', loc, re.IGNORECASE))
    # Non-China → skip
    if not is_china and _NON_CHINA_LOCATIONS.search(loc):
        return False
    # China Remote → keep
    if is_china and is_remote:
        return True
    # Shanghai (any work mode) → keep
    if is_shanghai:
        return True
    # Other Chinese cities, not remote → skip
    if is_china and not is_remote:
        return False
    return True  # ambiguous, keep

def should_fetch_jd(title, company):
    title_lower = title.lower()
    if has_chinese(title):
        return False
    for kw in TITLE_SKIP_KEYWORDS:
        if kw.lower() in title_lower:
            return False
    return True

# ─── EXCEL OUTPUT ─────────────────────────────────────────────────────────

def _save_excel(jobs, output_path):
    """Save jobs list to Excel (used for incremental saves during scoring)."""
    df = pd.DataFrame(jobs, columns=[
        "relevance_score", "relevance_reason", "match_tags",
        "title", "company", "location", "date_text",
        "easy_apply", "search_term", "url", "description"
    ])
    df.columns = [
        "相关性评分", "评分理由", "匹配关键词",
        "职位名称", "公司", "地点", "发布时间",
        "Easy Apply", "搜索词", "链接", "职位描述"
    ]
    df.to_excel(output_path, index=False, sheet_name="职位列表")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    driver = create_driver()
    all_jobs = []
    seen_ids = load_seen_ids()
    print(f"Already seen: {len(seen_ids)} jobs (will skip)")

    try:
        login(driver)

        # Two search rounds: Shanghai (all) + China (remote only)
        search_rounds = [
            (SEARCH_LOCATION, False, "Shanghai"),
            ("China",         True,  "China Remote"),
        ]

        for round_location, round_remote, round_label in search_rounds:
            print(f"\n{'#'*60}")
            print(f"# Round: {round_label}")
            print(f"{'#'*60}")

            for term in SEARCH_TERMS:
                print(f"\n{'='*60}")
                print(f"Searching: {term} ({round_label})")
                try:
                    url = build_search_url(term, round_location, DATE_FILTER, remote_only=round_remote)
                    driver.get(url)
                    time.sleep(3)

                    jobs = get_job_cards(driver, MAX_JOBS_PER_SEARCH)
                    print(f"  Found {len(jobs)} jobs")

                    skipped = 0
                    for i, job in enumerate(jobs):
                        job["search_term"] = f"{term} ({round_label})"
                        if job["job_id"] in seen_ids:
                            skipped += 1
                            continue
                        seen_ids[job["job_id"]] = datetime.now().isoformat()  # mark as seen
                        if not is_acceptable_location(job["location"]):
                            print(f"  [{i+1}/{len(jobs)}] ✗ SKIP (地点) {job['title']} @ {job['company']} ({job['location']})")
                            continue
                        if not should_fetch_jd(job["title"], job["company"]):
                            print(f"  [{i+1}/{len(jobs)}] ✗ SKIP {job['title']} @ {job['company']}")
                            continue
                        # Check title against industry exclude list
                        if EXCLUDE_INDUSTRIES.search(job["title"]):
                            print(f"  [{i+1}/{len(jobs)}] ✗ EXCLUDE (industry) {job['title']} @ {job['company']}")
                            continue
                        job["description"] = get_job_description(driver, job["url"])
                        time.sleep(1)
                        # Check JD against industry exclude list
                        if job["description"] and EXCLUDE_INDUSTRIES.search(job["description"][:500]):
                            print(f"  [{i+1}/{len(jobs)}] ✗ EXCLUDE (JD) {job['title']} @ {job['company']}")
                            continue
                        # Tag relevance
                        jd_text = job["title"] + " " + job.get("description", "")
                        hits = RELEVANT_KEYWORDS.findall(jd_text)
                        job["relevance_hits"] = len(hits)
                        print(f"  [{i+1}/{len(jobs)}] ✓ {job['title']} @ {job['company']} (relevance: {len(hits)})")
                        all_jobs.append(job)
                    if skipped:
                        print(f"  ↩ Skipped {skipped} already-seen jobs")
                    save_seen_ids(seen_ids)  # persist after each search term
                except Exception as e:
                    print(f"  ⚠ Error searching '{term}': {e}")
                    print(f"  Skipping to next search term...")
                    save_seen_ids(seen_ids)
                    continue

        # Deduplicate by job_id AND by title+company combo
        seen = set()
        seen_title_company = set()
        unique_jobs = []
        for j in all_jobs:
            key_tc = (j["title"].strip().lower(), j["company"].strip().lower())
            if j["job_id"] not in seen and key_tc not in seen_title_company:
                seen.add(j["job_id"])
                seen_title_company.add(key_tc)
                unique_jobs.append(j)

        print(f"\n{'='*60}")
        print(f"Total unique jobs collected: {len(unique_jobs)}")

        output_path = os.path.join(os.path.dirname(__file__), OUTPUT_FILE)

        if ENABLE_SCORING:
            print("Scoring with Claude...")
            for i, job in enumerate(unique_jobs):
                if not job["description"]:
                    job["relevance_score"] = 0
                    job["relevance_reason"] = "Skipped (title filter)"
                    job["match_tags"] = ""
                    continue
                print(f"  Scoring [{i+1}/{len(unique_jobs)}]: {job['title']} @ {job['company']}")
                score, reason, tags = score_job(job["title"], job["company"], job["description"])
                job["relevance_score"] = score
                job["relevance_reason"] = reason
                job["match_tags"] = tags
                # Incremental save after each scoring
                _save_excel(unique_jobs[:i+1], output_path)
            unique_jobs.sort(key=lambda x: -x["relevance_score"])
        else:
            print("Scoring disabled (ENABLE_SCORING=False), skipping Claude API calls.")
            # Sort by relevance keyword hits (descending)
            unique_jobs.sort(key=lambda x: -x.get("relevance_hits", 0))
            for job in unique_jobs:
                job["relevance_score"] = job.get("relevance_hits", 0)
                job["relevance_reason"] = ""
                job["match_tags"] = ""

        # Build DataFrame
        df = pd.DataFrame(unique_jobs, columns=[
            "relevance_score", "relevance_reason", "match_tags",
            "title", "company", "location", "date_text",
            "easy_apply", "search_term", "url", "description"
        ])
        df.columns = [
            "相关性评分", "评分理由", "匹配关键词",
            "职位名称", "公司", "地点", "发布时间",
            "Easy Apply", "搜索词", "链接", "职位描述"
        ]

        # Write to Excel with formatting
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="职位列表")
            ws = writer.sheets["职位列表"]

            # Column widths
            ws.column_dimensions["A"].width = 10  # 评分
            ws.column_dimensions["B"].width = 40  # 评分理由
            ws.column_dimensions["C"].width = 30  # 关键词
            ws.column_dimensions["D"].width = 35  # 职位名称
            ws.column_dimensions["E"].width = 25  # 公司
            ws.column_dimensions["F"].width = 20  # 地点
            ws.column_dimensions["G"].width = 15  # 时间
            ws.column_dimensions["H"].width = 12  # Easy Apply
            ws.column_dimensions["I"].width = 25  # 搜索词
            ws.column_dimensions["J"].width = 50  # 链接
            ws.column_dimensions["K"].width = 60  # 描述

            # Color rows by score
            from openpyxl.styles import PatternFill, Font
            green  = PatternFill("solid", fgColor="C6EFCE")
            yellow = PatternFill("solid", fgColor="FFEB9C")
            red    = PatternFill("solid", fgColor="FFC7CE")

            if ENABLE_SCORING:
                for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                    score_val = row[0].value
                    if not isinstance(score_val, (int, float)):
                        continue
                    if score_val >= 7:
                        fill = green
                    elif score_val >= 5:
                        fill = yellow
                    else:
                        fill = red
                    for cell in row:
                        cell.fill = fill

        save_seen_ids(seen_ids)

        print(f"\n✅ Done! Results saved to: {output_path}")
        print(f"   {len(unique_jobs)} new jobs collected")
        if ENABLE_SCORING:
            print(f"   {len(df[df['相关性评分'] >= 7])} high-relevance jobs (score 7+)")
            print(f"   {len(df[df['相关性评分'] >= 5])} medium-relevance jobs (score 5-6)")
            print(f"   {len(df[df['相关性评分'] < 5])} low-relevance jobs (score <5)")

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
