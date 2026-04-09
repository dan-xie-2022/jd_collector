'''
Backfill job descriptions for jobs already collected in an Excel file.
Reads the Excel, fetches missing JDs via Selenium, and saves back.
'''

import os
import re
import time
import pandas as pd
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

LINKEDIN_EMAIL    = os.environ["LINKEDIN_EMAIL"]
LINKEDIN_PASSWORD = os.environ["LINKEDIN_PASSWORD"]

# ─── Which Excel file to backfill ────────────────────────────────────────────
import sys
if len(sys.argv) > 1:
    EXCEL_FILE = sys.argv[1]
else:
    # Find the most recent job_results file
    files = sorted([f for f in os.listdir(os.path.dirname(os.path.abspath(__file__))) if f.startswith("job_results_") and f.endswith(".xlsx")])
    if not files:
        print("No job_results_*.xlsx found.")
        sys.exit(1)
    EXCEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), files[-1])
    print(f"Using: {EXCEL_FILE}")

JD_SELECTORS = [
    ".jobs-description__content",
    ".jobs-description-content__text",
    ".show-more-less-html__markup",
    ".jobs-box__html-content",
    "#job-details",
    "article .description",
    "[class*='jobs-description']",
]

def create_driver():
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    proxy = os.environ.get("HTTP_PROXY", "")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    return webdriver.Chrome(options=options)

def login(driver):
    print("Logging in...")
    driver.get("https://www.linkedin.com/login")
    wait = WebDriverWait(driver, 30)
    wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(LINKEDIN_EMAIL)
    driver.find_element(By.ID, "password").send_keys(LINKEDIN_PASSWORD)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    time.sleep(5)
    if "checkpoint" in driver.current_url or "challenge" in driver.current_url:
        print("⚠ Security check detected. Complete it in the browser.")
        input("Press Enter after verification...")
    print("Logged in.")

def get_job_description(driver, url):
    """Extract JD by finding the longest text blocks on the page (LinkedIn obfuscates class names)."""
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.texts = []
            self.current = ''
            self.skip = False
        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style', 'nav', 'header', 'footer'):
                self.skip = True
        def handle_endtag(self, tag):
            if tag in ('script', 'style', 'nav', 'header', 'footer'):
                self.skip = False
            text = self.current.strip()
            if len(text) > 100:
                self.texts.append(text)
            self.current = ''
        def handle_data(self, data):
            if not self.skip:
                self.current += data

    try:
        driver.get(url)
        time.sleep(4)
        parser = TextExtractor()
        parser.feed(driver.page_source)
        # Sort by length desc, take the top blocks and join them as the JD
        parser.texts.sort(key=len, reverse=True)
        # Typically the top 2-4 longest blocks are the actual JD content
        jd_parts = [t for t in parser.texts[:4] if len(t) > 150]
        return "\n\n".join(jd_parts)[:3000] if jd_parts else ""
    except Exception as e:
        print(f"  Error: {e}")
        return ""

def main():
    df = pd.read_excel(EXCEL_FILE)
    df['职位描述'] = df['职位描述'].astype(str).replace('nan', '')
    def has_chinese(text):
        return bool(re.search(r'[\u4e00-\u9fff]', str(text)))

    needs_jd = df[
        (df['职位描述'].isna() | (df['职位描述'] == '')) &
        (df['链接'].notna()) & (df['链接'] != '') &
        (~df['职位名称'].apply(has_chinese))  # skip Chinese titles
    ]

    if needs_jd.empty:
        print("All jobs already have descriptions!")
        return

    print(f"Need to fetch JD for {len(needs_jd)} jobs")

    driver = create_driver()
    try:
        login(driver)
        fetched = 0
        for idx, row in needs_jd.iterrows():
            title = row['职位名称']
            url = row['链接']
            print(f"  [{fetched+1}/{len(needs_jd)}] {title}")
            desc = get_job_description(driver, url)
            if desc:
                df.at[idx, '职位描述'] = desc
                fetched += 1
                print(f"    ✓ Got {len(desc)} chars")
            else:
                print(f"    ✗ No description found")
            time.sleep(1)

        # Save back
        df.to_excel(EXCEL_FILE, index=False, sheet_name="职位列表")
        print(f"\n✅ Done! Updated {fetched} job descriptions in {EXCEL_FILE}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
