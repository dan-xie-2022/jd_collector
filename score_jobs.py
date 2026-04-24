'''
Score jobs in an existing Excel file using DeepSeek API.
Usage: python3 score_jobs.py [excel_file]
'''

import os
import sys
import re
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
LIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "List")

# ─── Which Excel file to score ────────────────────────────────────────────────
if len(sys.argv) > 1:
    EXCEL_FILE = sys.argv[1]
else:
    files = sorted([f for f in os.listdir(LIST_DIR) if f.startswith("job_results_") and f.endswith(".xlsx")])
    if not files:
        print("No job_results_*.xlsx found in List/.")
        sys.exit(1)
    EXCEL_FILE = os.path.join(LIST_DIR, files[-1])
    print(f"Using: {EXCEL_FILE}")

# ─── Resume (copied from collect_jobs.py) ─────────────────────────────────────
from collect_jobs import RESUME_SUMMARY, _SCORE_SYSTEM

# ─── DeepSeek client ──────────────────────────────────────────────────────────
_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

def score_job(title, company, description):
    try:
        msg = _client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=200,
            messages=[
                {"role": "system", "content": _SCORE_SYSTEM},
                {"role": "user", "content": (
                    f"JOB:\nTitle: {title}\nCompany: {company}\n"
                    f"Description (excerpt):\n{description[:8000]}"
                )},
            ],
        )
        text = msg.choices[0].message.content.strip()
        score_match  = re.search(r"SCORE:\s*(\d+)", text)
        reason_match = re.search(r"REASON:\s*(.+)", text)
        tags_match   = re.search(r"MATCH_TAGS:\s*(.+)", text)
        score  = int(score_match.group(1))  if score_match  else 5
        reason = reason_match.group(1).strip() if reason_match else ""
        tags   = tags_match.group(1).strip()   if tags_match   else ""
        return score, reason, tags
    except Exception as e:
        print(f"  DeepSeek error: {e}")
        return 5, "Could not score", ""

def main():
    df = pd.read_excel(EXCEL_FILE)
    df['职位描述'] = df['职位描述'].astype(str).replace('nan', '')

    needs_scoring = df[df['职位描述'].str.len() > 100]
    print(f"Jobs with JD to score: {len(needs_scoring)}")

    for idx, row in needs_scoring.iterrows():
        title   = str(row['职位名称']).split('\n')[0]
        company = str(row['公司'])
        jd      = str(row['职位描述'])
        print(f"  [{idx+1}/{len(df)}] {title} @ {company}")
        score, reason, tags = score_job(title, company, jd)
        df.at[idx, '相关性评分'] = score
        df.at[idx, '评分理由']   = reason
        df.at[idx, '匹配关键词'] = tags
        print(f"    → Score: {score} | {reason}")

    df.sort_values('相关性评分', ascending=False, inplace=True)
    df.to_excel(EXCEL_FILE, index=False, sheet_name="职位列表")
    print(f"\n✅ Done! Scores saved to {EXCEL_FILE}")

if __name__ == "__main__":
    main()
