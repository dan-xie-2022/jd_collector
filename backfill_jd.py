'''
Backfill job descriptions for jobs already collected in an Excel file.
Reads the Excel, fetches missing JDs via Selenium, and saves back.
'''

import os
import re
import sys
import pandas as pd
from dotenv import load_dotenv
from utils import create_driver, login, get_job_description

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

LINKEDIN_EMAIL    = os.environ["LINKEDIN_EMAIL"]
LINKEDIN_PASSWORD = os.environ["LINKEDIN_PASSWORD"]

# ─── Which Excel file to backfill ────────────────────────────────────────────
LIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "List")

if len(sys.argv) > 1:
    EXCEL_FILE = sys.argv[1]
else:
    # Find the most recent job_results file in List/
    files = sorted([f for f in os.listdir(LIST_DIR) if f.startswith("job_results_") and f.endswith(".xlsx")])
    if not files:
        print("No job_results_*.xlsx found in List/.")
        sys.exit(1)
    EXCEL_FILE = os.path.join(LIST_DIR, files[-1])
    print(f"Using: {EXCEL_FILE}")


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
        login(driver, LINKEDIN_EMAIL, LINKEDIN_PASSWORD)
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
