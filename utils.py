"""
Shared utilities for LinkedIn Job Collector scripts.
"""

import os
import time
from html.parser import HTMLParser

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException


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


def login(driver, email, password):
    print("Logging in to LinkedIn...")
    driver.get("https://www.linkedin.com/login")
    wait = WebDriverWait(driver, 30)
    wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(email)
    driver.find_element(By.ID, "password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    time.sleep(5)
    if "checkpoint" in driver.current_url or "challenge" in driver.current_url:
        print("⚠ LinkedIn security check detected. Please complete it manually in the browser.")
        input("Press Enter after you've completed the verification...")
    print("Logged in.")


class _TextExtractor(HTMLParser):
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


def get_job_description(driver, url):
    """Fetch JD using the logged-in Selenium browser.
    LinkedIn obfuscates CSS class names, so we use rendered element text."""
    try:
        driver.get(url)
        time.sleep(6)

        # Strategy 1: find the smallest div whose rendered text contains "About the job"
        # Debug data confirms this works — LinkedIn renders JD text in a ~4000 char div
        for marker in ["About the job", "Job description", "About The Job"]:
            try:
                divs = driver.find_elements(By.CSS_SELECTOR, "div, section, article")
                candidates = []
                for div in divs:
                    try:
                        text = div.text.strip()
                    except Exception:
                        continue
                    if marker in text and 500 < len(text) < 8000:
                        candidates.append((len(text), text))
                if candidates:
                    # Pick the div with the most content AFTER the marker (= main JD, not sidebar snippet)
                    best = max(candidates, key=lambda x: len(x[1][x[1].find(marker) + len(marker):]))
                    raw = best[1]
                    jd = raw[raw.find(marker) + len(marker):].strip()
                    if len(jd) > 100:
                        return jd[:8000]
            except Exception:
                continue

        # Strategy 2: grab longest text block from page source (last resort)
        parser = _TextExtractor()
        parser.feed(driver.page_source)
        parser.texts.sort(key=len, reverse=True)
        parts = [t for t in parser.texts[:4] if len(t) > 150]
        return "\n\n".join(parts)[:3000] if parts else ""
    except Exception:
        return ""
