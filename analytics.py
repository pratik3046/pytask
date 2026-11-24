"""
Analytics Reporting Dashboard Module.
Computes outreach metrics across recruiters.csv and email_sent_history.csv,
and generates a self-contained, responsive, dark-themed HTML dashboard (analytics_report.html).
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd

from db_util import load_recruiters, load_sent_history

BASE_DIR = Path(__file__).resolve().parent
ANALYTICS_HTML_PATH = BASE_DIR / "analytics_report.html"


def compute_metrics() -> Dict[str, Any]:
    """Computes comprehensive outreach analytics."""
    rec_df = load_recruiters()
    sent_df = load_sent_history()

    # Recruiter scraping metrics
    total_scraped = len(rec_df)
    pending_count = len(rec_df[rec_df["email_sent_status"] == "pending"]) if not rec_df.empty else 0
    mismatched_count = len(rec_df[rec_df["email_sent_status"] == "mismatched_role"]) if not rec_df.empty else 0
    scraped_sent_count = len(rec_df[rec_df["email_sent_status"] == "sent"]) if not rec_df.empty else 0
    unique_recruiters = rec_df["recruiter_email"].nunique() if not rec_df.empty else 0

    # Email dispatch & conversion metrics
    total_dispatched = len(sent_df[sent_df["email_sent_status"] == "sent"]) if not sent_df.empty else 0
    total_failed = len(sent_df[sent_df["email_sent_status"] == "failed"]) if not sent_df.empty else 0
    total_followups = int(sent_df["followup_count"].sum()) if not sent_df.empty else 0
    total_replies = int(sent_df["replied"].sum()) if not sent_df.empty else 0

    reply_rate = round((total_replies / total_dispatched * 100), 1) if total_dispatched > 0 else 0.0
    followup_rate = round((total_followups / total_dispatched * 100), 1) if total_dispatched > 0 else 0.0

    # Candidate Breakdown
    candidate_stats: List[Dict[str, Any]] = []
    if not sent_df.empty:
        grouped = sent_df[sent_df["email_sent_status"] == "sent"].groupby("candidate")
        for cand, group in grouped:
            c_sent = len(group)
            c_replies = int(group["replied"].sum())
            c_followups = int(group["followup_count"].sum())
            c_rate = round((c_replies / c_sent * 100), 1) if c_sent > 0 else 0.0
            candidate_stats.append({
                "candidate": cand,
                "sent": c_sent,
                "followups": c_followups,
                "replies": c_replies,
                "reply_rate": c_rate
            })

    # Recent Dispatches
    recent_records: List[Dict[str, Any]] = []
    if not sent_df.empty:
        sorted_sent = sent_df.sort_values(by="timestamp", ascending=False).head(15)
        recent_records = sorted_sent.to_dict(orient="records")

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_scraped": total_scraped,
        "pending_count": pending_count,
        "mismatched_count": mismatched_count,
        "scraped_sent_count": scraped_sent_count,
        "unique_recruiters": unique_recruiters,
        "total_dispatched": total_dispatched,
        "total_failed": total_failed,
        "total_followups": total_followups,
        "total_replies": total_replies,
        "reply_rate": reply_rate,
        "followup_rate": followup_rate,
        "candidate_stats": candidate_stats,
        "recent_records": recent_records
    }


def generate_analytics_html(output_path: Path = ANALYTICS_HTML_PATH) -> Path:
    """Renders metrics into a modern dark-themed HTML report."""
    metrics = compute_metrics()

    cand_rows_html = ""
    for cs in metrics["candidate_stats"]:
        cand_rows_html += f"""
        <tr>
            <td style="font-weight: 600; color: #60a5fa;">{cs['candidate']}</td>
            <td>{cs['sent']}</td>
            <td>{cs['followups']}</td>
            <td><span class="badge badge-success">{cs['replies']}</span></td>
            <td><strong>{cs['reply_rate']}%</strong></td>
        </tr>
        """

    if not cand_rows_html:
        cand_rows_html = '<tr><td colspan="5" style="text-align:center; color:#94a3b8;">No dispatch data logged yet.</td></tr>'

    recent_rows_html = ""
    for r in metrics["recent_records"]:
        reply_badge = '<span class="badge badge-success">Replied</span>' if int(r.get('replied', 0)) == 1 else '<span class="badge badge-muted">Pending</span>'
        status_color = "#34d399" if r.get('email_sent_status') == 'sent' else "#f87171"
        recent_rows_html += f"""
        <tr>
            <td style="font-size: 0.85rem; color: #94a3b8;">{r.get('timestamp', '')}</td>
            <td><strong>{r.get('recruiter_name', 'Recruiter')}</strong><br><small style="color: #64748b;">{r.get('recruiter_email', '')}</small></td>
            <td><span class="role-pill">{r.get('role', '')}</span></td>
            <td>{r.get('candidate', '')}</td>
            <td style="color: {status_color}; font-weight: 500;">{r.get('email_sent_status', '')}</td>
            <td>{r.get('followup_count', 0)}</td>
            <td>{reply_badge}</td>
        </tr>
        """

    if not recent_rows_html:
        recent_rows_html = '<tr><td colspan="7" style="text-align:center; color:#94a3b8;">No sent outreach history found.</td></tr>'

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>C2C Outreach Automation Analytics</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #0b0f19;
            --bg-card: #111827;
            --bg-card-hover: #1f2937;
            --border-color: #374151;
            --text-primary: #f9fafb;
            --text-secondary: #9ca3af;
            --accent-blue: #3b82f6;
            --accent-green: #10b981;
            --accent-purple: #8b5cf6;
            --accent-amber: #f59e0b;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: var(--bg-base);
            color: var(--text-primary);
            padding: 2.5rem 1.5rem;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
            margin-bottom: 2rem;
        }}
        .title-group h1 {{
            font-size: 1.75rem;
            font-weight: 700;
            background: linear-gradient(135deg, #60a5fa 0%, #a78bfa 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .title-group p {{
            color: var(--text-secondary);
            font-size: 0.875rem;
            margin-top: 0.25rem;
        }}
        .timestamp {{
            font-size: 0.8rem;
            color: #64748b;
            background: rgba(255,255,255,0.03);
            padding: 0.4rem 0.8rem;
            border-radius: 6px;
            border: 1px solid #1f2937;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            position: relative;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
        }}
        .card::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0; height: 3px;
            background: var(--accent-blue);
        }}
        .card.card-green::before {{ background: var(--accent-green); }}
        .card.card-purple::before {{ background: var(--accent-purple); }}
        .card.card-amber::before {{ background: var(--accent-amber); }}

        .card-label {{
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            font-weight: 600;
        }}
        .card-value {{
            font-size: 2rem;
            font-weight: 700;
            margin: 0.5rem 0 0.2rem;
            color: var(--text-primary);
        }}
        .card-subtext {{
            font-size: 0.8rem;
            color: #64748b;
        }}
        .section-header {{
            font-size: 1.15rem;
            font-weight: 600;
            margin: 2rem 0 1rem;
            color: #e2e8f0;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        .table-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 2.5rem;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.9rem;
        }}
        th {{
            background: #1e293b;
            color: #cbd5e1;
            padding: 0.85rem 1.25rem;
            font-weight: 600;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid var(--border-color);
        }}
        td {{
            padding: 1rem 1.25rem;
            border-bottom: 1px solid #1f2937;
            color: #e2e8f0;
        }}
        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}
        .badge {{
            display: inline-block;
            padding: 0.25rem 0.6rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        .badge-success {{
            background: rgba(16, 185, 129, 0.2);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }}
        .badge-muted {{
            background: rgba(100, 116, 139, 0.2);
            color: #94a3b8;
            border: 1px solid rgba(100, 116, 139, 0.3);
        }}
        .role-pill {{
            background: #1e293b;
            color: #93c5fd;
            font-size: 0.78rem;
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            border: 1px solid #334155;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-group">
                <h1>C2C Recruitment Outreach Analytics</h1>
                <p>Real-time campaign performance and recruiter engagement dashboard</p>
            </div>
            <div class="timestamp">
                Updated: {metrics['timestamp']}
            </div>
        </header>

        <!-- Metrics Overview Grid -->
        <div class="grid">
            <div class="card card-purple">
                <div class="card-label">Total Posts Scraped</div>
                <div class="card-value">{metrics['total_scraped']}</div>
                <div class="card-subtext">{metrics['pending_count']} pending | {metrics['mismatched_count']} filtered</div>
            </div>
            <div class="card">
                <div class="card-label">Total Emails Dispatched</div>
                <div class="card-value">{metrics['total_dispatched']}</div>
                <div class="card-subtext">{metrics['total_failed']} failed dispatches</div>
            </div>
            <div class="card card-amber">
                <div class="card-label">Threaded Follow-Ups</div>
                <div class="card-value">{metrics['total_followups']}</div>
                <div class="card-subtext">{metrics['followup_rate']}% of initial outreach</div>
            </div>
            <div class="card card-green">
                <div class="card-label">Verified Replies</div>
                <div class="card-value">{metrics['total_replies']}</div>
                <div class="card-subtext">{metrics['reply_rate']}% positive recruiter response rate</div>
            </div>
        </div>

        <!-- Candidate Performance Breakdown -->
        <div class="section-header">Candidate Outreach Breakdown</div>
        <div class="table-card">
            <table>
                <thead>
                    <tr>
                        <th>Candidate</th>
                        <th>Emails Sent</th>
                        <th>Follow-Ups Sent</th>
                        <th>Replies</th>
                        <th>Reply Rate</th>
                    </tr>
                </thead>
                <tbody>
                    {cand_rows_html}
                </tbody>
            </table>
        </div>

        <!-- Recent Outreach Activity -->
        <div class="section-header">Recent Outreach Activity & Status</div>
        <div class="table-card">
            <table>
                <thead>
                    <tr>
                        <th>Timestamp</th>
                        <th>Recruiter</th>
                        <th>Target Role</th>
                        <th>Candidate</th>
                        <th>Status</th>
                        <th>Follow-Ups</th>
                        <th>Reply Flag</th>
                    </tr>
                </thead>
                <tbody>
                    {recent_rows_html}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"[analytics] Dashboard exported to: {output_path}")
    return output_path


if __name__ == "__main__":
    generate_analytics_html()
