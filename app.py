"""
LinkedIn Job Collector — Streamlit Web App
Run with: streamlit run app.py
"""

import glob
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime

import pandas as pd
import streamlit as st

import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "webapp_config.json")
APPS_FILE = os.path.join(BASE_DIR, "applications.json")
LIST_DIR = os.path.join(BASE_DIR, "List")
LOG_FILE = os.path.join(BASE_DIR, "run.log")

# ─── One-time migration from Excel + applications.json ───────────────────────

def _run_migration():
    migrated = db.migrate_from_excel(LIST_DIR)
    applied  = db.migrate_from_applications_json(APPS_FILE)
    return migrated, applied

if "db_migrated" not in st.session_state:
    _run_migration()
    st.session_state.db_migrated = True

# ─── Config helpers ───────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ─── Applications helpers ─────────────────────────────────────────────────────

def load_applications() -> list:
    if os.path.exists(APPS_FILE):
        with open(APPS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_applications(apps: list):
    with open(APPS_FILE, "w", encoding="utf-8") as f:
        json.dump(apps, f, indent=2, ensure_ascii=False)

def get_excel_files() -> list:
    return sorted(glob.glob(os.path.join(LIST_DIR, "job_results_*.xlsx")), reverse=True)

# ─── Job runner ───────────────────────────────────────────────────────────────

_run_lock = threading.Lock()
_job_running = False  # module-level, persists across Streamlit reruns

def _run_collect_job():
    global _job_running
    with _run_lock:
        if _job_running:
            return
        _job_running = True
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                ["python3", os.path.join(BASE_DIR, "collect_jobs.py")],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=BASE_DIR,
            )
            proc.wait()
    finally:
        _job_running = False

# ─── APScheduler (module-level singleton) ────────────────────────────────────

_scheduler = None

def _get_scheduler():
    global _scheduler
    if _scheduler is None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            _scheduler = BackgroundScheduler(daemon=True)
            _scheduler.start()
        except ImportError:
            pass
    return _scheduler

def _apply_schedule(cfg: dict):
    sched = _get_scheduler()
    if sched is None:
        return False
    sched.remove_all_jobs()
    if cfg.get("schedule_enabled"):
        t = cfg.get("schedule_time", "09:00")
        h, m = t.split(":")
        sched.add_job(_run_collect_job, "cron", hour=int(h), minute=int(m), id="daily_run")
    return True

# Apply schedule on startup
_apply_schedule(load_config())

# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Job Collector", page_icon="💼", layout="wide")
st.title("💼 LinkedIn Job Collector")

tab1, tab2, tab3, tab4 = st.tabs(["📊 职位列表", "📋 申请追踪", "⚙️ 设置", "🚀 运行 & 定时"])

# ─── Tab 1: Dashboard ─────────────────────────────────────────────────────────

import re as _re

def _clean_title(s: str) -> str:
    s = str(s).strip()
    s = _re.sub(r"\s*with verification\s*$", "", s, flags=_re.IGNORECASE).strip()
    # Remove simple duplication: "Foo Bar Foo Bar" → "Foo Bar"
    mid = len(s) // 2
    if mid > 10 and s[:mid].strip() == s[mid:].strip():
        s = s[:mid].strip()
    return s

def _extract_id(url: str):
    m = _re.search(r"/jobs/view/(\d+)", str(url))
    return m.group(1) if m else None

def _score_style(score: int) -> tuple[str, str]:
    if score >= 7:
        return "#d4edda", "#155724"   # green
    if score >= 5:
        return "#fff3cd", "#856404"   # yellow
    if score >= 4:
        return "#fde8d8", "#7d3c00"   # orange
    return "#f8d7da", "#721c24"       # red

_STATUS_BUTTONS = [
    ("📤 已申请",   "已投递"),
    ("📞 面试中",   "HR联系"),
    ("❌ 已拒绝",   "挂了"),
    ("😐 不感兴趣", "不感兴趣"),
]

_STATUS_BADGE = {
    "已投递":   ("📤 已申请",   "#d4edda", "#155724"),
    "HR联系":   ("📞 面试中",   "#cce5ff", "#004085"),
    "一面":     ("1️⃣ 一面",    "#cce5ff", "#004085"),
    "二面":     ("2️⃣ 二面",    "#cce5ff", "#004085"),
    "终面":     ("🏁 终面",    "#cce5ff", "#004085"),
    "offer":    ("🎉 Offer",   "#d4edda", "#155724"),
    "挂了":     ("❌ 已拒绝",  "#f8d7da", "#721c24"),
    "不感兴趣": ("😐 不感兴趣","#f0f0f0", "#888888"),
    "放弃":     ("🚫 放弃",    "#f5f5f5", "#888"),
    "待投递":   ("⏳ 待投递",  "#fff3cd", "#856404"),
}


def _upsert_application(jid, title, company, url, score, reason, new_status):
    apps = load_applications()
    existing = next((a for a in apps if a.get("job_id") == jid), None)
    if existing:
        existing["status"]     = new_status
        existing["updated_at"] = datetime.now().isoformat()
    else:
        apps.append({
            "job_id":       jid,
            "title":        title,
            "company":      company,
            "url":          url,
            "applied_date": datetime.now().strftime("%Y-%m-%d"),
            "status":       new_status,
            "score":        score,
            "score_reason": reason,
            "notes":        "",
            "skip_reason":  None,
            "updated_at":   datetime.now().isoformat(),
        })
    save_applications(apps)


def _remove_application(jid):
    apps = load_applications()
    apps = [a for a in apps if a.get("job_id") != jid]
    save_applications(apps)


def _render_cards(jobs: list[dict]):
    for row in jobs:
        score      = int(row.get("score", 0))
        title      = _clean_title(row.get("title", ""))
        company    = str(row.get("company", ""))
        location   = str(row.get("location", ""))
        reason     = str(row.get("score_reason", ""))
        tags       = str(row.get("match_tags", ""))
        url        = str(row.get("url", ""))
        easy       = bool(row.get("easy_apply", False))
        bg, fg     = _score_style(score)
        jid        = str(row.get("job_id", "")) or _extract_id(url) or f"{title}_{company}"
        cur_status = row.get("status")

        with st.container(border=True):
            c_score, c_body, c_link = st.columns([0.7, 5.5, 1])

            c_score.markdown(
                f"<div style='background:{bg};color:{fg};border-radius:10px;"
                f"padding:10px 6px;text-align:center;font-size:26px;font-weight:bold;"
                f"line-height:1.2'>{score}<br>"
                f"<span style='font-size:11px;font-weight:normal'>/ 10</span></div>",
                unsafe_allow_html=True,
            )

            badge = " &nbsp;⚡ <b>Easy Apply</b>" if easy else ""
            c_body.markdown(f"**{title}**{badge}", unsafe_allow_html=True)
            c_body.caption(f"🏢 {company}　　📍 {location}")
            if reason and reason != "nan":
                c_body.markdown(
                    f"<p style='color:#444;font-size:0.88em;margin:4px 0 2px'>{reason}</p>",
                    unsafe_allow_html=True,
                )
            if tags and tags != "nan":
                c_body.markdown(
                    f"<p style='color:#888;font-size:0.8em;margin:0'>🏷 {tags}</p>",
                    unsafe_allow_html=True,
                )

            # Status buttons row
            sb_cols = c_body.columns([1, 1, 1, 1])
            for col_idx, (label, status_val) in enumerate(_STATUS_BUTTONS):
                is_current = cur_status == status_val or (
                    status_val == "HR联系" and cur_status in ("HR联系", "一面", "二面", "终面")
                )
                btn_style = "primary" if is_current else "secondary"
                if sb_cols[col_idx].button(
                    label,
                    key=f"status_{jid}_{status_val}",
                    type=btn_style,
                    use_container_width=True,
                ):
                    if is_current:
                        db.update_status(jid, None)
                    else:
                        db.update_status(jid, status_val)
                    st.rerun()

            if url.startswith("http"):
                c_link.markdown(
                    f"<div style='padding-top:18px;text-align:center'>"
                    f"<a href='{url}' target='_blank' style='text-decoration:none;"
                    f"background:#f0f2f6;border-radius:6px;padding:6px 12px;"
                    f"color:#333;font-size:0.9em'>查看职位 →</a></div>",
                    unsafe_allow_html=True,
                )

with tab1:
    stats = db.get_stats()
    if stats["total"] == 0:
        st.info("还没有数据，请先在「运行 & 定时」中触发一次搜索。")
    else:
        # Filters
        fc1, fc2, fc3, fc4 = st.columns([2, 1, 1, 1])
        day_options = {"今天": 1, "最近 3 天": 3, "最近 7 天": 7, "最近 30 天": 30, "全部": 0}
        days = day_options[fc1.selectbox("时间范围", list(day_options.keys()), index=2)]
        min_score = fc2.slider("最低评分", 0, 10, 4)
        fc3.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        only_easy = fc3.checkbox("只看 Easy Apply", value=False)
        fc4.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        hide_tracked = fc4.checkbox("隐藏已标记", value=False)

        status_f = None if not hide_tracked else []
        all_jobs = db.get_jobs(min_score=min_score, days=days, only_easy=only_easy, status_filter=status_f)

        main_jobs = [j for j in all_jobs if j["score"] >= min_score]
        low_jobs  = db.get_jobs(min_score=0, days=days, only_easy=only_easy) if min_score > 0 else []
        low_jobs  = [j for j in low_jobs if j["score"] < min_score]

        # Header
        hc1, hc2 = st.columns([3, 1])
        hc1.caption(
            f"**{len(main_jobs)}** 个职位（评分 ≥ {min_score}）　·　"
            f"{len(low_jobs)} 个低分已折叠　·　数据库共 **{stats['total']}** 条"
        )
        very_high = [j for j in main_jobs if j["score"] >= 7]
        if very_high:
            if hc2.button(f"🔗 打开 {len(very_high)} 个高分链接"):
                for j in very_high:
                    if j["url"].startswith("http"):
                        subprocess.Popen(["open", j["url"]])
                st.success(f"已打开 {len(very_high)} 个链接")

        _render_cards(main_jobs)

        if low_jobs:
            with st.expander(f"📉 低分职位（评分 < {min_score}）— {len(low_jobs)} 个", expanded=False):
                _render_cards(low_jobs)

# ─── Tab 2: Applications ──────────────────────────────────────────────────────

with tab2:
    STATUS_OPTIONS = ["待投递", "已投递", "HR联系", "一面", "二面", "终面", "offer", "挂了", "放弃"]
    STATUS_EMOJI = {
        "待投递": "⏳", "已投递": "📤", "HR联系": "📞",
        "一面": "1️⃣", "二面": "2️⃣", "终面": "🏁",
        "offer": "🎉", "挂了": "❌", "放弃": "🚫",
    }

    stats = db.get_stats()
    by_status = stats["by_status"]

    if stats["tracked"] == 0:
        st.info("还没有申请记录。在「职位列表」中点击状态按钮即可添加。")
    else:
        # Summary metrics
        metric_cols = st.columns(len(STATUS_OPTIONS))
        for i, s in enumerate(STATUS_OPTIONS):
            metric_cols[i].metric(f"{STATUS_EMOJI.get(s, '')} {s}", by_status.get(s, 0))

        st.divider()

        filter_status = st.multiselect(
            "筛选状态",
            STATUS_OPTIONS,
            default=["已投递", "HR联系", "一面", "二面", "终面", "offer"],
        )

        tracked_jobs = db.get_jobs(
            min_score=0,
            status_filter=filter_status if filter_status else STATUS_OPTIONS,
            order_by="score DESC, status_updated DESC",
        )
        tracked_jobs = [j for j in tracked_jobs if j.get("status")]

        for job in tracked_jobs:
            jid    = str(job.get("job_id", ""))
            cur_st = job.get("status", "待投递")
            url    = job.get("url", "")

            c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 1, 1, 1, 2])
            c1.write(f"**{_clean_title(job.get('title', ''))}**")
            c2.write(job.get("company", ""))

            new_status = c3.selectbox(
                "状态", STATUS_OPTIONS,
                index=STATUS_OPTIONS.index(cur_st) if cur_st in STATUS_OPTIONS else 0,
                key=f"appstatus_{jid}",
                label_visibility="collapsed",
            )
            c4.write(f"⭐ {job.get('score', '-')}")
            c5.write(job.get("applied_date", "") or "")
            if url.startswith("http"):
                c6.markdown(f"[查看职位]({url})")

            if new_status != cur_st:
                db.update_status(jid, new_status)
                st.rerun()

# ─── Tab 3: Settings ──────────────────────────────────────────────────────────

with tab3:
    cfg = load_config()

    st.subheader("简历")
    uploaded_cv = st.file_uploader("上传 CV (PDF) — 将自动提取文字", type=["pdf"])
    if uploaded_cv is not None:
        try:
            import pdfplumber
            with pdfplumber.open(uploaded_cv) as pdf:
                extracted = "\n".join(p.extract_text() or "" for p in pdf.pages)
            cfg["resume_summary"] = extracted
            st.success(f"已提取 {len(extracted)} 个字符，请在下方确认后保存。")
        except ImportError:
            st.error("请先安装 pdfplumber：pip install pdfplumber")

    cfg["resume_summary"] = st.text_area(
        "简历内容（可直接编辑）",
        value=cfg.get("resume_summary", ""),
        height=300,
    )

    st.divider()
    st.subheader("搜索配置")

    loc_options = ["London", "United Kingdom", "Shanghai", "Beijing", "Shenzhen"]
    cur_loc = cfg.get("search_location", "London")
    cfg["search_location"] = st.selectbox(
        "搜索地点", loc_options,
        index=loc_options.index(cur_loc) if cur_loc in loc_options else 0,
    )

    date_options = ["Past 24 hours", "Past week", "Past month"]
    cur_date = cfg.get("date_filter", "Past 24 hours")
    cfg["date_filter"] = st.selectbox(
        "时间范围", date_options,
        index=date_options.index(cur_date) if cur_date in date_options else 0,
    )

    cfg["max_jobs_per_search"] = st.slider(
        "每个搜索词最多抓取", 1, 20, cfg.get("max_jobs_per_search", 5)
    )
    cfg["enable_scoring"] = st.checkbox("启用 AI 评分（DeepSeek）", cfg.get("enable_scoring", True))

    st.divider()
    st.subheader("搜索词（每行一个）")
    terms_raw = st.text_area(
        "搜索词列表",
        value="\n".join(cfg.get("search_terms", [])),
        height=250,
        label_visibility="collapsed",
    )
    cfg["search_terms"] = [t.strip() for t in terms_raw.splitlines() if t.strip()]

    st.divider()
    st.subheader("邮件通知")
    st.caption(
        "需要 Gmail App Password（不是登录密码）。"
        "前往 Google 账户 → 安全 → 两步验证 → 应用专用密码 生成一个，"
        "然后填入 `.env` 文件：`GMAIL_ADDRESS=你的邮箱` 和 `GMAIL_APP_PASSWORD=16位密码`"
    )
    email_cfg = cfg.get("email", {})
    email_cfg["recipient"] = st.text_input(
        "接收邮件地址", value=email_cfg.get("recipient", "danxie229@gmail.com")
    )
    email_cfg["min_score"] = st.slider(
        "只推送评分 ≥", 1, 10, email_cfg.get("min_score", 5)
    )
    cfg["email"] = email_cfg

    if st.button("📧 测试发送邮件"):
        from notifier import send_notification
        with st.spinner("发送中…"):
            ok, err = send_notification(min_score=email_cfg.get("min_score", 5))
        if ok:
            st.success("发送成功！请检查收件箱。")
        else:
            st.error(f"发送失败：{err}")

    if st.button("💾 保存设置", type="primary"):
        save_config(cfg)
        st.success("设置已保存！下次运行时生效。")

# ─── launchd helpers ──────────────────────────────────────────────────────────

PLIST_LABEL = "com.jobcollector.daily"
PLIST_PATH  = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_LABEL}.plist")


def _write_plist(hour: int, minute: int):
    python_path = sys.executable
    script_path = os.path.join(BASE_DIR, "run_daily.py")
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{BASE_DIR}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>{LOG_FILE}</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>"""
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    with open(PLIST_PATH, "w") as f:
        f.write(plist)


def _launchd_install(hour: int, minute: int) -> str:
    _write_plist(hour, minute)
    r = subprocess.run(
        ["launchctl", "load", "-w", PLIST_PATH],
        capture_output=True, text=True,
    )
    return r.stderr.strip() or "ok"


def _launchd_uninstall() -> str:
    r = subprocess.run(
        ["launchctl", "unload", "-w", PLIST_PATH],
        capture_output=True, text=True,
    )
    if os.path.exists(PLIST_PATH):
        os.remove(PLIST_PATH)
    return r.stderr.strip() or "ok"


def _launchd_active() -> bool:
    r = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True, text=True,
    )
    return r.returncode == 0


# ─── Tab 4: Run & Schedule ────────────────────────────────────────────────────

with tab4:
    cfg = load_config()
    col_run, col_sched = st.columns(2)

    with col_run:
        st.subheader("手动运行")
        is_running = _job_running

        if is_running:
            st.warning("🔄 正在运行中…")
            if st.button("🔁 刷新状态"):
                st.rerun()
        else:
            if st.button("▶ 立即运行", type="primary"):
                t = threading.Thread(target=_run_collect_job, daemon=True)
                t.start()
                time.sleep(0.5)
                st.success("已启动！Chrome 浏览器将自动打开，完成后结果保存到 List/ 并发送邮件。")
                st.rerun()

        if os.path.exists(LOG_FILE):
            mtime = os.path.getmtime(LOG_FILE)
            last_run = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            st.caption(f"上次运行：{last_run}")
            with st.expander("查看运行日志", expanded=is_running):
                with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                    log_text = f.read()
                tail = log_text[-3000:] if len(log_text) > 3000 else log_text
                st.code(tail, language=None)

    with col_sched:
        st.subheader("每日定时运行（launchd）")

        active = _launchd_active()
        if active:
            st.success("✅ 定时任务已启用（关闭终端也会运行）")
        else:
            st.info("⏸ 定时任务未启用")

        sched_time_str = cfg.get("schedule_time", "09:00")
        sched_time = st.time_input(
            "每天运行时间",
            value=datetime.strptime(sched_time_str, "%H:%M").time(),
        )

        bc1, bc2 = st.columns(2)
        if bc1.button("✅ 启用定时", type="primary", disabled=active):
            err = _launchd_install(sched_time.hour, sched_time.minute)
            cfg["schedule_time"] = sched_time.strftime("%H:%M")
            cfg["schedule_enabled"] = True
            save_config(cfg)
            if err == "ok":
                st.success(f"已设置每天 {sched_time.strftime('%H:%M')} 自动运行")
            else:
                st.warning(f"launchctl: {err}")
            st.rerun()

        if bc2.button("🛑 停用定时", disabled=not active):
            _launchd_uninstall()
            cfg["schedule_enabled"] = False
            save_config(cfg)
            st.success("定时已停用")
            st.rerun()

        st.divider()
        st.caption(
            "launchd 是 macOS 原生调度器，**不依赖终端是否打开**。\n\n"
            "每次运行完成后会自动发送邮件通知（需在「设置」中配置 Gmail）。"
        )
