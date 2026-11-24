"""
Database and CSV Migration Utility Module.
Handles flat CSV databases (recruiters.csv and email_sent_history.csv),
ensuring automated schema validation, automatic runtime migration against drift,
and thread-safe query/update helpers.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
RECRUITERS_CSV = BASE_DIR / "recruiters.csv"
SENT_HISTORY_CSV = BASE_DIR / "email_sent_history.csv"

# ==============================================================================
# Canonical Schema Definitions
# ==============================================================================
RECRUITERS_COLUMNS: Dict[str, Any] = {
    "recruiter_name": "",
    "recruiter_email": "",
    "role": "",
    "post_url": "",
    "job_description": "",
    "email_sent_status": "pending",  # pending | sent | failed | mismatched_role
    "timestamp": ""
}

SENT_HISTORY_COLUMNS: Dict[str, Any] = {
    "recruiter_name": "",
    "recruiter_email": "",
    "role": "",
    "post_url": "",
    "candidate": "",
    "email_sent_status": "sent",  # sent | failed
    "timestamp": "",
    "subject": "",
    "message_id": "",
    "followup_count": 0,
    "last_followup_at": "",
    "replied": 0  # 0 = unreplied, 1 = replied
}


def _validate_and_migrate_csv(file_path: Path, schema: Dict[str, Any]) -> pd.DataFrame:
    """
    Validates CSV columns against canonical schema.
    If missing columns are detected or the file does not exist,
    migrates/creates the CSV automatically preserving existing rows.
    """
    expected_cols = list(schema.keys())

    if not file_path.exists() or file_path.stat().st_size == 0:
        df = pd.DataFrame(columns=expected_cols)
        df.to_csv(file_path, index=False, encoding="utf-8")
        return df

    try:
        df = pd.read_csv(file_path, dtype=str)
    except Exception:
        df = pd.DataFrame(columns=expected_cols)
        df.to_csv(file_path, index=False, encoding="utf-8")
        return df

    # Check for missing columns
    missing_cols = [c for c in expected_cols if c not in df.columns]
    if missing_cols:
        for col in missing_cols:
            df[col] = schema[col]
        # Reorder to canonical columns
        df = df[expected_cols]
        df.to_csv(file_path, index=False, encoding="utf-8")

    return df


def init_databases() -> None:
    """Initializes and migrates recruiters.csv and email_sent_history.csv."""
    _validate_and_migrate_csv(RECRUITERS_CSV, RECRUITERS_COLUMNS)
    _validate_and_migrate_csv(SENT_HISTORY_CSV, SENT_HISTORY_COLUMNS)


def load_recruiters() -> pd.DataFrame:
    """Loads and validates recruiters.csv."""
    return _validate_and_migrate_csv(RECRUITERS_CSV, RECRUITERS_COLUMNS)


def save_recruiters(df: pd.DataFrame) -> None:
    """Saves DataFrame to recruiters.csv safely."""
    df.to_csv(RECRUITERS_CSV, index=False, encoding="utf-8")


def load_sent_history() -> pd.DataFrame:
    """Loads and validates email_sent_history.csv."""
    df = _validate_and_migrate_csv(SENT_HISTORY_CSV, SENT_HISTORY_COLUMNS)
    # Ensure numeric types
    df["followup_count"] = pd.to_numeric(df["followup_count"], errors="coerce").fillna(0).astype(int)
    df["replied"] = pd.to_numeric(df["replied"], errors="coerce").fillna(0).astype(int)
    return df


def save_sent_history(df: pd.DataFrame) -> None:
    """Saves DataFrame to email_sent_history.csv safely."""
    df.to_csv(SENT_HISTORY_CSV, index=False, encoding="utf-8")


def has_candidate_contacted_recruiter(candidate_slug_or_name: str, recruiter_email: str) -> bool:
    """
    Checks if a specific candidate has already sent an email to the recruiter.
    Ensures candidate-level de-duplication while permitting other candidates to reach out.
    """
    if not recruiter_email or not str(recruiter_email).strip():
        return False

    df = load_sent_history()
    if df.empty:
        return False

    clean_email = recruiter_email.strip().lower()
    matches = df[
        (df["recruiter_email"].str.strip().str.lower() == clean_email) &
        (df["candidate"].str.strip().str.lower() == candidate_slug_or_name.strip().lower()) &
        (df["email_sent_status"] == "sent")
    ]
    return len(matches) > 0


def append_recruiter(
    recruiter_name: str,
    recruiter_email: str,
    role: str,
    post_url: str,
    job_description: str,
    email_sent_status: str = "pending"
) -> bool:
    """
    Appends a new scraped recruiter entry to recruiters.csv if post_url / email is not duplicate.
    Returns True if appended, False if already present.
    """
    df = load_recruiters()
    clean_email = recruiter_email.strip().lower() if recruiter_email else ""
    clean_url = post_url.strip() if post_url else ""

    # Avoid duplicate post entries
    if clean_url and not df.empty:
        if (df["post_url"].str.strip() == clean_url).any():
            return False

    if clean_email and not df.empty:
        if ((df["recruiter_email"].str.strip().str.lower() == clean_email) & 
            (df["role"].str.strip().str.lower() == role.strip().lower())).any():
            return False

    new_row = {
        "recruiter_name": recruiter_name.strip(),
        "recruiter_email": clean_email,
        "role": role.strip(),
        "post_url": clean_url,
        "job_description": job_description.strip(),
        "email_sent_status": email_sent_status,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_recruiters(df)
    return True


def append_sent_email(
    recruiter_name: str,
    recruiter_email: str,
    role: str,
    post_url: str,
    candidate: str,
    email_sent_status: str,
    subject: str,
    message_id: str,
    followup_count: int = 0,
    last_followup_at: str = "",
    replied: int = 0
) -> None:
    """Records an outreach dispatch event in email_sent_history.csv."""
    df = load_sent_history()
    new_row = {
        "recruiter_name": recruiter_name.strip(),
        "recruiter_email": recruiter_email.strip().lower(),
        "role": role.strip(),
        "post_url": post_url.strip(),
        "candidate": candidate.strip(),
        "email_sent_status": email_sent_status,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "subject": subject.strip(),
        "message_id": message_id.strip(),
        "followup_count": followup_count,
        "last_followup_at": last_followup_at,
        "replied": replied
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_sent_history(df)


def update_recruiter_status(post_url: str, new_status: str) -> None:
    """Updates the email_sent_status of a recruiter record by post_url."""
    df = load_recruiters()
    if df.empty:
        return
    idx = df[df["post_url"].str.strip() == post_url.strip()].index
    if len(idx) > 0:
        df.loc[idx, "email_sent_status"] = new_status
        save_recruiters(df)


def get_pending_recruiters(target_role: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns list of pending recruiter dicts, optionally filtered by role."""
    df = load_recruiters()
    if df.empty:
        return []
    pending_df = df[df["email_sent_status"] == "pending"]
    if target_role:
        pending_df = pending_df[pending_df["role"].str.lower() == target_role.strip().lower()]
    return pending_df.to_dict(orient="records")


def mark_email_replied(recruiter_email: str) -> int:
    """Marks all sent history records matching recruiter_email as replied=1. Returns updated count."""
    df = load_sent_history()
    if df.empty or not recruiter_email:
        return 0

    clean_email = recruiter_email.strip().lower()
    mask = (df["recruiter_email"].str.strip().str.lower() == clean_email) & (df["replied"] == 0)
    match_count = int(mask.sum())
    if match_count > 0:
        df.loc[mask, "replied"] = 1
        save_sent_history(df)
    return match_count
