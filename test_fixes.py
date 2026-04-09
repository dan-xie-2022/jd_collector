'''
Test script for all fixes:
- Test 1: geoId in search URLs
- Test 2: Location filter (non-China jobs filtered out)
- Test 3: Title skip filter (customer service jobs filtered)
- Test 4: JD fetching via Selenium
'''
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from collect_jobs import build_search_url, GEO_IDS, is_acceptable_location, should_fetch_jd

# ─── Test 1: geoId ──────────────────────────────────────────────────────────

print("=" * 60)
print("Test 1: geoId in search URLs")
print("=" * 60)

test_cases = [
    ("Solutions Engineer", "Shanghai", "Past 24 hours"),
    ("Customer Success",   "China",    "Past week"),
    ("TAM",                "Shenzhen", "Past month"),
    ("Engineer",           "Tokyo",    "Past 24 hours"),
]

all_pass = True
for term, loc, date in test_cases:
    url = build_search_url(term, loc, date)
    geo_id = GEO_IDS.get(loc, "")
    if geo_id:
        ok = f"geoId={geo_id}" in url
    else:
        ok = "geoId=" not in url
    status = "PASS" if ok else "FAIL"
    if not ok: all_pass = False
    print(f"  [{status}] {loc}: {url[-30:]}")

print(f"  Result: {'ALL PASS' if all_pass else 'SOME FAILED'}\n")

# ─── Test 2: Location filter ────────────────────────────────────────────────

print("=" * 60)
print("Test 2: Location filter (non-China = skip)")
print("=" * 60)

location_tests = [
    ("Shanghai, Shanghai, China (Hybrid)",    True),
    ("Shanghai, Shanghai, China (On-site)",   True),
    ("Shanghai, Shanghai, China (Remote)",    True),
    ("Beijing, China",                        True),
    ("深圳",                                   True),
    ("Hangzhou, Zhejiang, China",             True),
    ("United States (Remote)",                False),
    ("NAMER (Remote)",                        False),
    ("New York, NY",                          False),  # ambiguous, should keep
    ("London, United Kingdom",                False),
    ("Singapore",                             False),
    ("Bangalore, India",                      False),
    ("",                                      True),   # unknown, keep
]

all_pass = True
for loc, expected in location_tests:
    result = is_acceptable_location(loc)
    ok = result == expected
    status = "PASS" if ok else "FAIL"
    if not ok: all_pass = False
    action = "keep" if result else "skip"
    print(f"  [{status}] \"{loc}\" → {action} (expected {'keep' if expected else 'skip'})")

print(f"  Result: {'ALL PASS' if all_pass else 'SOME FAILED'}\n")

# ─── Test 3: Title skip filter (customer service) ───────────────────────────

print("=" * 60)
print("Test 3: Title skip filter (customer service jobs)")
print("=" * 60)

title_tests = [
    ("Customer Success Engineer",             True),   # should keep
    ("Customer Success Manager",              True),   # should keep
    ("Solutions Engineer",                    True),   # should keep
    ("电话客服",                                False),  # should skip (Chinese)
    ("淘宝客服",                                False),  # should skip (Chinese)
    ("Customer Service Representative",       False),  # should skip
    ("Call Center Agent",                     False),  # should skip
    ("在线客服",                                False),  # should skip (Chinese)
    ("Technical Account Manager",             True),   # should keep
    ("Software Engineer",                     False),  # should skip
    ("VP of Engineering",                     False),  # should skip
]

all_pass = True
for title, expected in title_tests:
    result = should_fetch_jd(title, "TestCo")
    ok = result == expected
    status = "PASS" if ok else "FAIL"
    if not ok: all_pass = False
    action = "keep" if result else "skip"
    print(f"  [{status}] \"{title}\" → {action} (expected {'keep' if expected else 'skip'})")

print(f"  Result: {'ALL PASS' if all_pass else 'SOME FAILED'}\n")

# ─── Test 4: JD fetching via Selenium ────────────────────────────────────────

print("=" * 60)
print("Test 4: JD fetching via Selenium")
print("=" * 60)

from collect_jobs import create_driver, login, get_job_description

test_urls = [
    ("Example Job 1", "https://www.linkedin.com/jobs/view/1234567890/"),
    ("Example Job 2", "https://www.linkedin.com/jobs/view/0987654321/"),
]

driver = create_driver()
try:
    login(driver)
    for name, url in test_urls:
        print(f"\n  Fetching: {name}")
        jd = get_job_description(driver, url)
        jd_len = len(jd)
        status = "PASS" if jd_len > 100 else "FAIL"
        print(f"  [{status}] Got {jd_len} chars")
        if jd_len > 0:
            print(f"  Preview: {jd[:150]}...")
finally:
    driver.quit()

print(f"\nAll tests done!")
