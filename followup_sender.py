"""
Threaded Follow-Up Engine.
Identifies unanswered outreach emails sent X days ago (default 7 days),
generates an AI value proposition matching the candidate to the target JD,
and dispatches threaded follow-up emails utilizing In-Reply-To and References headers.
"""

import os
import re
import time
import requests
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import make_msgid, formatdate
from typing import List, Dict, Any, Tuple
from pathlib import Path

from dotenv import load_dotenv

from client_config import (
    CLIENTS,
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_TEAM_LEAD_EMAIL,
    SCRATCH_DIR
)
from db_util import load_sent_history, save_sent_history
from gmail_sender import create_smtp_connection, sanitize_filename

load_dotenv()

GMAIL_EMAIL = os.getenv("GMAIL_EMAIL", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")


def generate_ai_value_proposition(candidate_name: str, role: str, original_subject: str) -> str:
    """
    Calls OpenRouter / Fallback LLM to generate a concise 1-to-2 sentence value proposition
    reiterating candidate strength and asking for an update on the C2C requirement.
    """
    if OPENROUTER_API_KEY and "your_" not in OPENROUTER_API_KEY:
        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            }
            prompt = (
                f"Write a 1-to-2 sentence compelling value proposition follow-up sentence from candidate {candidate_name} "
                f"to a recruiter regarding role '{role}'. Highlight immediate availability, strong hands-on architecture, "
                f"and prompt them politely for feedback on the C2C candidacy. Keep it professional, concise, and direct."
            )
            payload = {
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content:
                    return content
        except Exception as exc:
            print(f"[followup_sender] OpenRouter value prop error: {exc}")

    # Deterministic high-converting fallback
    return (
        f"I wanted to quickly follow up on my previous note. With my extensive hands-on experience in {role} "
        f"and immediate availability on Corp-to-Corp (C2C), I am confident I can hit the ground running and "
        f"deliver immediate value to your client."
    )


def get_unanswered_outreach_records(days_threshold: int = 7) -> List[Dict[str, Any]]:
    """
    Queries email_sent_history.csv for outreach records where:
    - email_sent_status == 'sent'
    - followup_count == 0
    - replied == 0
    - timestamp was sent >= days_threshold days ago
    """
    df = load_sent_history()
    if df.empty:
        return []

    cutoff_date = datetime.now() - timedelta(days=days_threshold)
    eligible_records = []

    for _, row in df.iterrows():
        if str(row.get("email_sent_status")) != "sent":
            continue
        if int(row.get("followup_count", 0)) > 0:
            continue
        if int(row.get("replied", 0)) != 0:
            continue

        raw_ts = str(row.get("timestamp", "")).strip()
        if not raw_ts:
            continue

        try:
            sent_dt = datetime.strptime(raw_ts, "%Y-%m-%d %H:%M:%S")
            if sent_dt <= cutoff_date:
                eligible_records.append(row.to_dict())
        except Exception:
            continue

    return eligible_records


def send_threaded_followup(record: Dict[str, Any], test_mode: bool = False) -> Tuple[bool, str]:
    """
    Dispatches a threaded follow-up email:
    - Uses In-Reply-To and References headers pointing to the original Message-ID
    - Subject: 'Re: <original_subject>'
    - Injects AI-generated value proposition
    - Re-attaches resume
    - Updates followup_count and last_followup_at
    """
    candidate_slug = record.get("candidate", "")
    cand_cfg = CLIENTS.get(candidate_slug)
    if not cand_cfg:
        return False, f"Unknown candidate: {candidate_slug}"

    recruiter_email = record.get("recruiter_email", "").strip().lower()
    recruiter_name = record.get("recruiter_name", "Hiring Recruiter")
    orig_msg_id = record.get("message_id", "").strip()
    orig_subject = record.get("subject", "C2C Requirement")
    role = record.get("role", "Senior Engineer")

    # Format threaded subject
    threaded_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"

    # AI Value proposition
    value_prop = generate_ai_value_proposition(cand_cfg["name"], role, orig_subject)

    greeting = recruiter_name if recruiter_name and recruiter_name != "Hiring Recruiter" else "Hiring Team"
    body = f"""Hi {greeting},

I hope your week is going well.

{value_prop}

Could you let me know if the position is still open or if you'd like to arrange a brief introductory call this week?

Thank you for your time and consideration.

Best regards,

{cand_cfg['name']}
{cand_cfg.get('phone', '')}
{cand_cfg.get('email', '')}
"""

    msg = MIMEMultipart()
    msg["From"] = f"{cand_cfg['name']} <{GMAIL_EMAIL}>"
    msg["To"] = recruiter_email
    msg["Subject"] = threaded_subject
    msg["Date"] = formatdate(localtime=True)

    # Threading Headers
    if orig_msg_id:
        # Ensure wrapped in brackets if needed
        clean_id = orig_msg_id if orig_msg_id.startswith("<") else f"<{orig_msg_id}>"
        msg["In-Reply-To"] = clean_id
        msg["References"] = clean_id

    new_msg_id = make_msgid(domain="gmail.com")
    msg["Message-ID"] = new_msg_id

    # CC & BCC
    cc_list = [cand_cfg["email"], DEFAULT_TEAM_LEAD_EMAIL]
    bcc_list = [DEFAULT_ADMIN_EMAIL]
    msg["Cc"] = ", ".join(cc_list)

    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Re-attach resume if available
    for tmpl in cand_cfg.get("resume_candidates", []):
        p = Path(tmpl)
        if p.exists() and p.suffix.lower() == ".pdf":
            try:
                with open(p, "rb") as f:
                    part = MIMEApplication(f.read(), _subtype="pdf")
                safe_name = sanitize_filename(f"{cand_cfg['name'].replace(' ', '_')}_Resume.pdf")
                part.add_header("Content-Disposition", "attachment", filename=safe_name)
                msg.attach(part)
                break
            except Exception:
                pass

    all_recipients = [recruiter_email] + cc_list + bcc_list

    if test_mode:
        print(f" [TEST MODE] Threaded follow-up simulated for {candidate_slug} -> <{recruiter_email}>")
        print(f"   Subject: {threaded_subject}")
        print(f"   In-Reply-To: {orig_msg_id}")
        _update_record_followup(recruiter_email, orig_msg_id)
        return True, "test_followup_simulated"

    try:
        server = create_smtp_connection()
        server.send_message(msg, from_addr=GMAIL_EMAIL, to_addrs=all_recipients)
        server.quit()

        _update_record_followup(recruiter_email, orig_msg_id)
        print(f" [+] Threaded follow-up sent to {recruiter_email} (In-Reply-To: {orig_msg_id})")
        time.sleep(3.0)
        return True, "followup_sent"
    except Exception as exc:
        print(f" [!] Follow-up send failed for {recruiter_email}: {exc}")
        return False, str(exc)


def _update_record_followup(recruiter_email: str, message_id: str):
    """Updates followup_count and last_followup_at in email_sent_history.csv."""
    df = load_sent_history()
    if df.empty:
        return
    mask = (df["recruiter_email"].str.strip().str.lower() == recruiter_email.strip().lower())
    if message_id:
        mask = mask & (df["message_id"].str.strip() == message_id.strip())

    idx = df[mask].index
    if len(idx) > 0:
        current_count = int(df.loc[idx[0], "followup_count"])
        df.loc[idx, "followup_count"] = current_count + 1
        df.loc[idx, "last_followup_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_sent_history(df)


def run_followups(days_threshold: int = 7, test_mode: bool = False) -> int:
    """Executes threaded follow-ups for all eligible unanswered outreach records."""
    records = get_unanswered_outreach_records(days_threshold=days_threshold)
    print(f"\n[followup_sender] Found {len(records)} outreach records eligible for follow-up (sent >= {days_threshold} days ago).")
    sent_count = 0
    for r in records:
        success, _ = send_threaded_followup(r, test_mode=test_mode)
        if success:
            sent_count += 1
    return sent_count


if __name__ == "__main__":
    run_followups(days_threshold=7)
