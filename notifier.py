"""
Email notifier — sends a summary of high-score jobs after each daily run.
Can be called standalone: python3 notifier.py
"""

import glob
import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "webapp_config.json")
LIST_DIR    = os.path.join(BASE_DIR, "List")


def _load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _clean_title(s: str) -> str:
    s = str(s).strip()
    s = re.sub(r"\s*with verification\s*$", "", s, flags=re.IGNORECASE).strip()
    mid = len(s) // 2
    if mid > 10 and s[:mid].strip() == s[mid:].strip():
        s = s[:mid].strip()
    return s


def _score_color(score: int) -> str:
    if score >= 7:
        return "#d4edda", "#155724"
    if score >= 5:
        return "#fff3cd", "#856404"
    if score >= 4:
        return "#fde8d8", "#7d3c00"
    return "#f8d7da", "#721c24"


def _build_html(jobs: list[dict], run_date: str) -> str:
    cards_html = ""
    for job in jobs:
        score   = job.get("score", 0)
        bg, fg  = _score_color(score)
        title   = job.get("title", "")
        company = job.get("company", "")
        loc     = job.get("location", "")
        reason  = job.get("reason", "")
        tags    = job.get("tags", "")
        url     = job.get("url", "")
        easy    = "⚡ Easy Apply &nbsp;" if job.get("easy_apply") else ""

        link_btn = (
            f'<a href="{url}" style="display:inline-block;margin-top:10px;'
            f'background:#4a90d9;color:white;text-decoration:none;padding:6px 14px;'
            f'border-radius:5px;font-size:13px">查看职位 →</a>'
            if url else ""
        )

        cards_html += f"""
        <div style="border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;
                    margin-bottom:14px;background:#fff;border-left:5px solid {bg}">
          <div style="display:flex;align-items:center;gap:14px">
            <div style="background:{bg};color:{fg};border-radius:8px;padding:8px 12px;
                        font-size:22px;font-weight:bold;min-width:48px;text-align:center;
                        line-height:1.1">{score}<br>
                <span style="font-size:10px;font-weight:normal">/10</span>
            </div>
            <div style="flex:1">
              <div style="font-size:16px;font-weight:600;color:#222">{title}
                <span style="font-size:12px;color:#888;font-weight:normal">&nbsp;{easy}</span>
              </div>
              <div style="color:#555;font-size:13px;margin-top:3px">🏢 {company} &nbsp;·&nbsp; 📍 {loc}</div>
            </div>
          </div>
          {"" if not reason else f'<p style="margin:10px 0 4px;color:#444;font-size:13px">{reason}</p>'}
          {"" if not tags  else f'<p style="margin:4px 0;color:#999;font-size:12px">🏷 {tags}</p>'}
          {link_btn}
        </div>"""

    return f"""
    <html><body style="font-family:Arial,sans-serif;background:#f5f6fa;padding:24px">
      <div style="max-width:680px;margin:auto">
        <h2 style="color:#333">💼 LinkedIn 职位日报 — {run_date}</h2>
        <p style="color:#666;font-size:14px">{len(jobs)} 个高分职位（评分 ≥ 5）</p>
        {cards_html}
        <p style="color:#aaa;font-size:12px;margin-top:24px">由 LinkedIn Job Collector 自动发送</p>
      </div>
    </body></html>"""


def send_notification(min_score: int = 5) -> bool:
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)
    cfg = _load_config()
    email_cfg = cfg.get("email", {})

    smtp_user = os.environ.get("GMAIL_ADDRESS", email_cfg.get("sender", ""))
    smtp_pass = os.environ.get("GMAIL_APP_PASSWORD", "")
    recipient = email_cfg.get("recipient", smtp_user)

    if not smtp_user or not smtp_pass:
        msg = f"GMAIL_ADDRESS={'set' if smtp_user else 'MISSING'}, GMAIL_APP_PASSWORD={'set' if smtp_pass else 'MISSING'}"
        print(f"⚠ Email not configured: {msg}")
        return False, msg

    # Find latest Excel
    files = sorted(glob.glob(os.path.join(LIST_DIR, "job_results_*.xlsx")), reverse=True)
    if not files:
        print("⚠ No Excel file found, skipping notification")
        return False

    df = pd.read_excel(files[0])
    df["相关性评分"] = pd.to_numeric(df.get("相关性评分", 0), errors="coerce").fillna(0)
    df = df[df["相关性评分"] >= min_score].sort_values("相关性评分", ascending=False)

    if df.empty:
        print(f"No jobs with score >= {min_score}, skipping notification")
        return True, ""

    jobs = [
        {
            "score":     int(row.get("相关性评分", 0)),
            "title":     _clean_title(row.get("职位名称", "")),
            "company":   str(row.get("公司", "")),
            "location":  str(row.get("地点", "")),
            "reason":    str(row.get("评分理由", "")),
            "tags":      str(row.get("匹配关键词", "")),
            "url":       str(row.get("链接", "")),
            "easy_apply": bool(row.get("Easy Apply", False)),
        }
        for _, row in df.iterrows()
    ]

    run_date = datetime.now().strftime("%Y-%m-%d")
    html = _build_html(jobs, run_date)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"💼 职位日报 {run_date} — {len(jobs)} 个高分职位"
    msg["From"]    = smtp_user
    msg["To"]      = recipient
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipient, msg.as_string())
        print(f"✅ Email sent to {recipient} ({len(jobs)} jobs)")
        return True, ""
    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False, str(e)


if __name__ == "__main__":
    send_notification()
