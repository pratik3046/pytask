"""
Gmail Outreach Sender Module.
Handles dynamic JD keyword hooking, candidate-level de-duplication,
SMTP transport with automatic STARTTLS/SSL fallback, MIME PDF attachment
sanitization, message-ID tracking, and rate throttling.
"""

import os
import re
import time
import smtplib
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import make_msgid, formatdate
from typing import Dict, Any, Optional, Tuple, List

from dotenv import load_dotenv

from client_config import (
    CLIENTS,
    CANDIDATE_ROLES,
    JD_KEYWORDS,
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_TEAM_LEAD_EMAIL,
    SCRATCH_DIR
)
from db_util import (
    has_candidate_contacted_recruiter,
    append_sent_email,
    update_recruiter_status
)
from resume_customizer import customize_resume

load_dotenv()

GMAIL_EMAIL = os.getenv("GMAIL_EMAIL", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")


def extract_top_jd_keywords(job_description: str, max_keywords: int = 3) -> List[str]:
    """
    Scans the scraped job description against the 60+ JD_KEYWORDS taxonomy
    and returns the top matching keywords in order of frequency/relevance.
    """
    matches = []
    lower_jd = job_description.lower()
    for kw in JD_KEYWORDS:
        pattern = rf"\b{re.escape(kw.lower())}\b"
        count = len(re.findall(pattern, lower_jd))
        if count > 0:
            matches.append((kw, count))

    # Sort by frequency descending
    matches.sort(key=lambda x: x[1], reverse=True)
    top = [m[0] for m in matches[:max_keywords]]
    if not top:
        top = ["Enterprise Architecture", "Cloud Engineering", "Full Lifecycle Delivery"]
    return top


def generate_outreach_subject(candidate_name: str, target_role: str, top_keywords: List[str]) -> str:
    """Constructs a high-converting, professional C2C email subject line."""
    kw_str = ", ".join(top_keywords[:2])
    return f"Corp-to-Corp (C2C): {candidate_name} - {target_role} ({kw_str})"


def generate_outreach_body(
    candidate_name: str,
    recruiter_name: str,
    target_role: str,
    top_keywords: List[str],
    candidate_profile: Dict[str, Any]
) -> str:
    """
    Generates a personalized C2C pitch email body injecting dynamic JD keyword hooks,
    candidate availability, work authorization, and contact details.
    """
    hook_keywords = ", ".join(top_keywords)
    hook_sentence = (
        f"My hands-on enterprise experience with {hook_keywords} maps directly to what you "
        f"are looking for in the {target_role} position you posted on LinkedIn."
    )

    greeting_name = recruiter_name if recruiter_name and recruiter_name != "Hiring Recruiter" else "Hiring Team"

    body = f"""Hi {greeting_name},

I hope this email finds you well.

I came across your recent LinkedIn post regarding the {target_role} requirement and wanted to reach out directly. {hook_sentence}

Here is a brief snapshot of my qualifications:
• Role / Focus: {candidate_profile.get('experience', 'Senior Engineer')}
• Core Skills: {hook_keywords}
• Work Authorization: {candidate_profile.get('work_auth', 'C2C Eligible')}
• Location: {candidate_profile.get('location', 'United States')} ({candidate_profile.get('relocation', 'Remote / Open to Relocation')})
• Availability: {candidate_profile.get('availability', 'Immediate')}

I have attached my tailored resume for your review. Please let me know if we can connect for a quick 5-minute call today to discuss how my background fits your client's needs on a Corp-to-Corp (C2C) basis.

Best regards,

{candidate_name}
{candidate_profile.get('phone', '')}
{candidate_profile.get('email', '')}
LinkedIn: {candidate_profile.get('linkedin', '')}
"""
    return body


def create_smtp_connection(timeout: int = 15) -> smtplib.SMTP:
    """
    Establishes an authenticated SMTP session with Gmail:
    Attempts STARTTLS on port 587 first; falls back automatically to SSL on port 465.
    """
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        raise ValueError("GMAIL_EMAIL or GMAIL_APP_PASSWORD not set in environment.")

    # 1. Primary: STARTTLS port 587
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=timeout)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
        return server
    except Exception as exc:
        print(f"[gmail_sender] STARTTLS (587) attempt failed: {exc}. Falling back to SSL (465)...")

    # 2. Fallback: SMTP_SSL port 465
    server_ssl = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=timeout)
    server_ssl.ehlo()
    server_ssl.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
    return server_ssl


def sanitize_filename(name: str) -> str:
    """Sanitizes filename strings to ASCII only to avoid 'noname' attachment headers."""
    clean = re.sub(r"[^\w\.-]", "_", name)
    return clean.encode("ascii", "ignore").decode("ascii")


def send_outreach_email(
    candidate_slug: str,
    recruiter_name: str,
    recruiter_email: str,
    role: str,
    post_url: str,
    job_description: str,
    test_mode: bool = False
) -> Tuple[bool, str]:
    """
    Dispatches a customized cold outreach email for a client candidate:
    - Verifies candidate-specific de-duplication
    - Dynamically hooks top 3 JD keywords
    - Customizes resume via resume_customizer (or client-specific hooks)
    - Constructs MIME email with non-ASCII sanitized attachment headers
    - Dispatches with CC (candidate, team lead) and BCC (admin)
    - Captures Message-ID and records in email_sent_history.csv
    - Updates recruiters.csv pipeline state
    - Sleeps 3 seconds between dispatches for rate throttling
    """
    cand_cfg = CLIENTS.get(candidate_slug)
    if not cand_cfg:
        return False, f"Unknown candidate slug '{candidate_slug}'"

    clean_recruiter_email = recruiter_email.strip().lower()

    # 1. Candidate-Level De-duplication Check
    if has_candidate_contacted_recruiter(candidate_slug, clean_recruiter_email):
        print(f"[gmail_sender] Skipped: Recruiter <{clean_recruiter_email}> already contacted by {candidate_slug}.")
        return False, "already_contacted"

    # 2. Dynamic JD Keyword Hooking
    top_kws = extract_top_jd_keywords(job_description, max_keywords=3)
    subject = generate_outreach_subject(cand_cfg["name"], role, top_kws)
    body = generate_outreach_body(cand_cfg["name"], recruiter_name, role, top_kws, cand_cfg)

    # 3. AI Resume Customization
    print(f"[gmail_sender] Customizing resume for {cand_cfg['name']} -> {role}...")
    custom_pdf_path = customize_resume(
        candidate_slug=candidate_slug,
        target_role=role,
        job_description=job_description
    )

    # 4. MIME Composition & Header Sanitization
    msg = MIMEMultipart()
    msg["From"] = f"{cand_cfg['name']} <{GMAIL_EMAIL}>"
    msg["To"] = clean_recruiter_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)

    # Generate unique Message-ID
    msg_id = make_msgid(domain="gmail.com")
    msg["Message-ID"] = msg_id

    # CC & BCC Routing
    cc_recipients = [cand_cfg["email"], DEFAULT_TEAM_LEAD_EMAIL]
    bcc_recipients = [DEFAULT_ADMIN_EMAIL]
    msg["Cc"] = ", ".join(cc_recipients)

    # Attach Body
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Attach Customized Resume with Unix Timestamp
    unix_ts = int(time.time())
    safe_name = sanitize_filename(f"{cand_cfg['name'].replace(' ', '_')}_Resume_{unix_ts}.pdf")

    try:
        with open(custom_pdf_path, "rb") as f:
            pdf_data = f.read()
        part = MIMEApplication(pdf_data, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=safe_name)
        msg.attach(part)
    except Exception as exc:
        print(f"[gmail_sender] Attachment error: {exc}")

    # Determine all recipients
    all_recipients = [clean_recruiter_email] + cc_recipients + bcc_recipients

    if test_mode:
        print(f" [TEST MODE] Simulated dispatch for {candidate_slug} to <{clean_recruiter_email}>")
        print(f"   Subject: {subject}")
        print(f"   Attachment: {safe_name}")
        append_sent_email(
            recruiter_name=recruiter_name,
            recruiter_email=clean_recruiter_email,
            role=role,
            post_url=post_url,
            candidate=candidate_slug,
            email_sent_status="sent",
            subject=subject,
            message_id=msg_id
        )
        update_recruiter_status(post_url, "sent")
        return True, "test_sent_simulated"

    # 5. SMTP Dispatch with Fallback
    try:
        server = create_smtp_connection()
        server.send_message(msg, from_addr=GMAIL_EMAIL, to_addrs=all_recipients)
        server.quit()
        print(f" [+] Email sent successfully to {clean_recruiter_email} (Msg-ID: {msg_id})")

        # Record in history
        append_sent_email(
            recruiter_name=recruiter_name,
            recruiter_email=clean_recruiter_email,
            role=role,
            post_url=post_url,
            candidate=candidate_slug,
            email_sent_status="sent",
            subject=subject,
            message_id=msg_id
        )
        update_recruiter_status(post_url, "sent")

        # Rate Throttling: 3s sleep
        time.sleep(3.0)
        return True, "sent"

    except Exception as exc:
        print(f" [!] Failed to send email to {clean_recruiter_email}: {exc}")
        append_sent_email(
            recruiter_name=recruiter_name,
            recruiter_email=clean_recruiter_email,
            role=role,
            post_url=post_url,
            candidate=candidate_slug,
            email_sent_status="failed",
            subject=subject,
            message_id=msg_id
        )
        update_recruiter_status(post_url, "failed")
        return False, str(exc)
