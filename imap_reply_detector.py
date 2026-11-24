"""
IMAP Recruiter Reply Detector Module.
Connects securely to Gmail IMAP over SSL (imap.gmail.com:993),
calculates dynamic search date boundaries based on unreplied sent history,
scans inbox headers in batches of 100, and updates email_sent_history.csv (replied = 1).
"""

import os
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from typing import List, Dict, Any, Set, Tuple
from pathlib import Path
from dotenv import load_dotenv

from db_util import load_sent_history, mark_email_replied

load_dotenv()

GMAIL_EMAIL = os.getenv("GMAIL_EMAIL", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")


def clean_header_text(header_val: str) -> str:
    """Decodes MIME encoded header strings."""
    if not header_val:
        return ""
    try:
        decoded_parts = decode_header(header_val)
        result = []
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(encoding or "utf-8", errors="replace"))
            else:
                result.append(str(part))
        return "".join(result)
    except Exception:
        return str(header_val)


def extract_email_address(from_header: str) -> str:
    """Extracts clean email address from 'Name <email@domain.com>' format."""
    if not from_header:
        return ""
    match = re.search(r"<([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>", from_header)
    if match:
        return match.group(1).lower().strip()
    # Check if raw email
    clean = from_header.strip().lower()
    if "@" in clean:
        parts = clean.split()
        for p in parts:
            if "@" in p:
                return p.strip("<>,;\"'").lower()
    return ""


def get_earliest_unreplied_date(default_days: int = 30) -> datetime:
    """
    Inspects email_sent_history.csv for the oldest sent timestamp where replied == 0.
    Returns datetime object for SEARCH SINCE boundary, defaulting to 30 days ago.
    """
    df = load_sent_history()
    fallback_dt = datetime.now() - timedelta(days=default_days)

    if df.empty:
        return fallback_dt

    unreplied_df = df[df["replied"] == 0]
    if unreplied_df.empty:
        return fallback_dt

    earliest_dt = None
    for ts in unreplied_df["timestamp"].dropna():
        try:
            dt = datetime.strptime(str(ts).strip(), "%Y-%m-%d %H:%M:%S")
            if earliest_dt is None or dt < earliest_dt:
                earliest_dt = dt
        except Exception:
            continue

    return earliest_dt if earliest_dt else fallback_dt


def get_contacted_recruiter_emails() -> Set[str]:
    """Returns set of all lowercase recruiter emails from email_sent_history.csv."""
    df = load_sent_history()
    if df.empty:
        return set()
    return set(df["recruiter_email"].dropna().str.strip().str.lower().unique())


def get_contacted_message_ids() -> Set[str]:
    """Returns set of all sent Message-IDs to cross-reference In-Reply-To headers."""
    df = load_sent_history()
    if df.empty:
        return set()
    clean_ids = set()
    for mid in df["message_id"].dropna().str.strip():
        clean_ids.add(mid.strip("<>"))
    return clean_ids


def scan_inbox_for_replies(batch_size: int = 100) -> int:
    """
    Connects to Gmail IMAP over SSL (read-only), searches for messages SINCE the earliest
    unreplied outreach date, fetches headers in batches of 100, matches sender or In-Reply-To
    against email_sent_history.csv, and flags replied = 1.
    """
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        print("[imap_reply_detector] GMAIL_EMAIL or GMAIL_APP_PASSWORD missing in .env.")
        return 0

    since_dt = get_earliest_unreplied_date()
    # Format IMAP date: DD-Mon-YYYY (e.g. 12-Jun-2026)
    imap_date_str = since_dt.strftime("%d-%b-%Y")

    contacted_emails = get_contacted_recruiter_emails()
    contacted_msg_ids = get_contacted_message_ids()

    if not contacted_emails:
        print("[imap_reply_detector] No contacted recruiters found in history to match.")
        return 0

    print(f"\n[imap_reply_detector] Connecting to imap.gmail.com:993 SSL...")
    print(f"[imap_reply_detector] Searching INBOX for messages SINCE {imap_date_str}...")

    total_matched = 0

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
        # Select INBOX in read-only mode so flags / unread statuses are not modified
        mail.select("INBOX", readonly=True)

        search_criteria = f'(SINCE "{imap_date_str}")'
        status, messages = mail.search(None, search_criteria)

        if status != "OK" or not messages[0]:
            print("[imap_reply_detector] No messages returned for search criteria.")
            mail.close()
            mail.logout()
            return 0

        msg_id_list = messages[0].split()
        total_msgs = len(msg_id_list)
        print(f"[imap_reply_detector] Retrieved {total_msgs} messages in date range. Scanning headers...")

        # Process in batches of batch_size
        for i in range(0, total_msgs, batch_size):
            batch = msg_id_list[i : i + batch_size]
            batch_str = b",".join(batch).decode("ascii")

            # Fetch RFC822 headers only for optimal throughput
            res, data = mail.fetch(batch_str, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT IN-REPLY-TO REFERENCES DATE)])")
            if res != "OK":
                continue

            for item in data:
                if not isinstance(item, tuple):
                    continue

                raw_header = item[1]
                msg = email.message_from_bytes(raw_header)

                from_hdr = clean_header_text(msg.get("From", ""))
                sender_email = extract_email_address(from_hdr)
                in_reply_to = clean_header_text(msg.get("In-Reply-To", "")).strip("<>")
                references = clean_header_text(msg.get("References", "")).strip()

                is_reply = False
                matched_recruiter = ""

                # 1. Match by sender email
                if sender_email and sender_email in contacted_emails:
                    is_reply = True
                    matched_recruiter = sender_email

                # 2. Match by In-Reply-To or References header
                if not is_reply and in_reply_to:
                    for tracked_id in contacted_msg_ids:
                        if tracked_id and (tracked_id in in_reply_to or tracked_id in references):
                            is_reply = True
                            # Find recruiter email associated with tracked_id
                            df = load_sent_history()
                            m = df[df["message_id"].str.strip("<>") == tracked_id]
                            if not m.empty:
                                matched_recruiter = m.iloc[0]["recruiter_email"]
                            break

                if is_reply and matched_recruiter:
                    updated = mark_email_replied(matched_recruiter)
                    if updated > 0:
                        total_matched += updated
                        print(f" [REPLY DETECTED] Recruiter <{matched_recruiter}> replied! Updated database.")

        mail.close()
        mail.logout()
        print(f"[imap_reply_detector] Inbox synchronization complete. {total_matched} records updated.\n")

    except Exception as exc:
        print(f"[imap_reply_detector] IMAP operation error: {exc}")

    return total_matched


if __name__ == "__main__":
    scan_inbox_for_replies()
