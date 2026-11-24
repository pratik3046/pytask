"""
Targeted LinkedIn Scraper Engine.
Automates headless Playwright extraction of US-based Corp-to-Corp (C2C) recruiter posts.
Executes Boolean search queries, scrolls feeds, extracts recruiter emails,
intercepts post permalinks via clipboard and DOM attributes, and applies strict filtering.
"""

import re
import sys
import time
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from playwright.sync_api import sync_playwright, BrowserContext, Page

from client_config import CANDIDATE_ROLES
from db_util import append_recruiter, load_recruiters

BASE_DIR = Path(__file__).resolve().parent
SESSION_FILE = BASE_DIR / "session.json"

# ==============================================================================
# Filtering Constants
# ==============================================================================
NON_USA_INDICATORS = [
    "india", "pune", "noida", "hyderabad", "bangalore", "bengaluru", "chennai", "gurgaon",
    "delhi", "mumbai", "kolkata", "ahmedabad", "canada", "toronto", "vancouver", "ontario",
    "philippines", "manila", "pakistan", "lahore", "karachi", "uk", "london", "dubai", "uae"
]

US_LOCATION_INDICATORS = [
    "usa", "united states", "remote - us", "us remote", "remote (us)", "c2c", "corp to corp",
    "corp-to-corp", "tx", "ca", "ny", "nj", "nc", "fl", "ga", "il", "va", "pa", "oh", "wa",
    "dallas", "austin", "houston", "irving", "plano", "san jose", "san francisco", "new york",
    "chicago", "atlanta", "charlotte", "seattle", "reston", "tampa", "boston", "phoenix",
    "denver", "los angeles", "california", "texas", "est", "pst", "cst", "mst"
]

BENCH_MARKETER_PHRASES = [
    "on my bench", "candidate available", "marketing my consultant", "hotlist",
    "available consultants", "bench candidates", "share your c2c requirements",
    "looking for c2c requirements", "bench marketer", "consultants available"
]

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9.%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def is_usa_job(text: str) -> bool:
    """
    Evaluates whether the post represents a US-based job.
    Rejects posts with non-US geographic indicators;
    Confirms presence of US indicators or explicit US C2C indicators.
    """
    lower = text.lower()
    for non_us in NON_USA_INDICATORS:
        # Match whole word boundary to avoid false positives (e.g. 'indiana' vs 'india')
        if re.search(rf"\b{re.escape(non_us)}\b", lower):
            return False

    for us_loc in US_LOCATION_INDICATORS:
        if re.search(rf"\b{re.escape(us_loc)}\b", lower):
            return True

    return False


def matches_strict_filter(job_text: str, candidate_slug: str) -> Tuple[bool, str]:
    """
    Applies strict post filtering:
    1. Rejects bench marketer spam
    2. Rejects non-USA positions
    3. Checks for domain conflicts (mismatched_role)
    Returns: (is_valid, status_or_reason)
    """
    lower = job_text.lower()

    # 1. Bench marketer spam exclusion
    for bench_phrase in BENCH_MARKETER_PHRASES:
        if bench_phrase in lower:
            return False, "bench_marketer_spam"

    # Candidate exclusion keywords
    cand_info = CANDIDATE_ROLES.get(candidate_slug, {})
    exclusions = cand_info.get("exclusion_keywords", [])
    for exc in exclusions:
        if re.search(rf"\b{re.escape(exc.lower())}\b", lower):
            return False, f"excluded_keyword_{exc}"

    # 2. USA Job check
    if not is_usa_job(job_text):
        return False, "non_usa_location"

    # 3. Domain mismatch check
    primary_role = cand_info.get("primary_role", "").lower()
    # If candidate is Java Engineer, reject obvious non-software or conflicting domains
    if "java" in primary_role:
        conflicting_domains = ["salesforce developer", "sap abap", "qa automation", "devops engineer", "scrum master"]
        for cd in conflicting_domains:
            if re.search(rf"\b{cd}\b", lower) and "java" not in lower:
                return False, "mismatched_role"
    elif "python" in primary_role:
        conflicting_domains = ["salesforce developer", "sap abap", "mainframe developer", ".net developer"]
        for cd in conflicting_domains:
            if re.search(rf"\b{cd}\b", lower) and "python" not in lower:
                return False, "mismatched_role"

    return True, "pending"


def clean_linkedin_url(url: str) -> str:
    """Strips tracking query parameters from LinkedIn post URLs."""
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return clean.rstrip("/")


def extract_recruiter_name_from_url(profile_or_post_url: str) -> str:
    """Extracts a human-readable recruiter name from a LinkedIn profile URL or post URL."""
    if not profile_or_post_url:
        return "Hiring Manager"
    match = re.search(r"/in/([a-zA-Z0-9_-]+)", profile_or_post_url)
    if match:
        raw_slug = match.group(1).replace("-", " ")
        cleaned = re.sub(r"\d+", "", raw_slug).strip().title()
        return cleaned if cleaned else "Hiring Recruiter"
    return "Hiring Recruiter"


class LinkedInScraper:
    """Automates targeted LinkedIn post scraping using Playwright."""

    def __init__(self, session_path: Path = SESSION_FILE, headless: bool = True):
        self.session_path = session_path
        self.headless = headless

    def scrape_posts_for_role(
        self,
        role: str,
        candidate_slug: str,
        max_posts: int = 15,
        scroll_count: int = 6
    ) -> List[Dict[str, Any]]:
        """
        Executes LinkedIn search for the role with C2C filters in the last 24 hours.
        Extracts recruiter emails, clean URLs, and job descriptions.
        """
        if not self.session_path.exists():
            print(f"\n[!] [linkedin_scraper] Authentication Error: Session file not found at {self.session_path}.")
            print("    LinkedIn requires an authenticated session to access content search results.")
            print("    To resolve this, please run Option 2 in main.py (or run 'python linkedin_login.py')")
            print("    to open the browser, authenticate, and save session.json.\n")
            return []

        # Boolean Search Query Construction
        query = f'"{role}" + C2C -hotlist -fulltime -"available consultants" -Bench'
        encoded_query = urllib.parse.quote_plus(query)
        search_url = (
            f"https://www.linkedin.com/search/results/content/?"
            f"keywords={encoded_query}&geoUrn=103644278&sortBy=%22date_posted%22&datePosted=%22past-24h%22"
        )

        extracted_records: List[Dict[str, Any]] = []

        print(f"\n[linkedin_scraper] Scraping role: '{role}' (Candidate: {candidate_slug})")
        print(f"[linkedin_scraper] Query: {query}")
        print(f"[linkedin_scraper] Search URL: {search_url}")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = browser.new_context(
                storage_state=str(self.session_path),
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                permissions=["clipboard-read", "clipboard-write"]
            )
            page = context.new_page()

            try:
                page.goto(search_url, timeout=45000, wait_until="domcontentloaded")
                time.sleep(3)

                # Scroll feed and expand truncated descriptions
                print(f"[linkedin_scraper] Scrolling feed ({scroll_count} iterations)...")
                for i in range(scroll_count):
                    page.evaluate("window.scrollBy(0, 1200);")
                    time.sleep(1.5)

                    # Force click "See more" buttons to reveal full post text
                    try:
                        see_more_buttons = page.locator(
                            "button.feed-shared-inline-show-more-text__see-more-less-toggle, "
                            "button[aria-label*='see more' i], "
                            "span:text-is('…see more'), "
                            "span:text-is('see more')"
                        )
                        btn_count = see_more_buttons.count()
                        for b in range(min(btn_count, 10)):
                            try:
                                see_more_buttons.nth(b).click(timeout=1000)
                            except Exception:
                                pass
                    except Exception:
                        pass

                # Locate post cards
                post_cards = page.locator("div.feed-shared-update-v2, div[data-urn*='urn:li:activity:']")
                card_count = post_cards.count()
                print(f"[linkedin_scraper] Identified {card_count} post cards in feed.")

                for idx in range(min(card_count, max_posts)):
                    card = post_cards.nth(idx)
                    try:
                        card.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass

                    post_text = card.inner_text()
                    if not post_text:
                        continue

                    # Email Extraction
                    recruiter_email = ""
                    # 1. Check mailto: links in the post card
                    try:
                        mailto_links = card.locator("a[href^='mailto:']")
                        if mailto_links.count() > 0:
                            raw_href = mailto_links.first.get_attribute("href") or ""
                            recruiter_email = raw_href.replace("mailto:", "").split("?")[0].strip()
                    except Exception:
                        pass

                    # 2. Regex scan tokens
                    if not recruiter_email:
                        email_matches = EMAIL_REGEX.findall(post_text)
                        # Filter out common false matches
                        valid_matches = [
                            em for em in email_matches
                            if not em.lower().endswith((".png", ".jpg", ".jpeg", ".webp", "@linkedin.com"))
                        ]
                        if valid_matches:
                            recruiter_email = valid_matches[0]

                    if not recruiter_email:
                        # Skip posts without an explicit email address
                        continue

                    # Recruiter Name and Profile URL
                    recruiter_name = "Hiring Recruiter"
                    try:
                        author_link = card.locator("a.app-aware-link[href*='/in/'], a[href*='/in/']").first
                        if author_link.is_visible():
                            profile_url = author_link.get_attribute("href") or ""
                            recruiter_name = extract_recruiter_name_from_url(profile_url)
                    except Exception:
                        pass

                    # Three-Dots Clipboard Interceptor & DOM Permalinks
                    post_url = ""
                    try:
                        # Clear clipboard before attempting copy
                        page.evaluate("navigator.clipboard.writeText('')")
                        control_menu_btn = card.locator(
                            "button[aria-label*='control menu for post' i], "
                            "button[aria-label*='options' i], "
                            "button.feed-shared-control-menu__trigger"
                        ).first

                        if control_menu_btn.is_visible():
                            control_menu_btn.click(timeout=1500)
                            time.sleep(0.5)

                            copy_option = page.locator(
                                "div[aria-label*='Copy link' i], "
                                "span:text-matches('Copy link to post', 'i'), "
                                "li:has-text('Copy link')"
                            ).first
                            if copy_option.is_visible():
                                copy_option.click(timeout=1500)
                                time.sleep(0.5)
                                copied_text = page.evaluate("navigator.clipboard.readText()")
                                if copied_text and "linkedin.com" in copied_text:
                                    post_url = clean_linkedin_url(copied_text)
                    except Exception:
                        pass

                    # Fallback DOM URL Resolution
                    if not post_url:
                        try:
                            urn_val = card.get_attribute("data-urn") or card.get_attribute("data-chameleon-urn") or ""
                            if "activity:" in urn_val:
                                act_id = urn_val.split("activity:")[1].split(",")[0]
                                post_url = f"https://www.linkedin.com/feed/update/urn:li:activity:{act_id}/"
                        except Exception:
                            pass

                    if not post_url:
                        post_url = f"https://www.linkedin.com/feed/update/urn:li:post_{int(time.time())}_{idx}"

                    # Strict Filtering
                    is_valid, status = matches_strict_filter(post_text, candidate_slug)
                    if not is_valid and status != "mismatched_role":
                        # Exclude spam or non-USA
                        continue

                    email_sent_status = "pending" if is_valid else "mismatched_role"

                    record = {
                        "recruiter_name": recruiter_name,
                        "recruiter_email": recruiter_email.lower(),
                        "role": role,
                        "post_url": post_url,
                        "job_description": post_text[:2000],  # Store sanitized snippet
                        "email_sent_status": email_sent_status
                    }

                    # Append to recruiters.csv flat database
                    appended = append_recruiter(
                        recruiter_name=record["recruiter_name"],
                        recruiter_email=record["recruiter_email"],
                        role=record["role"],
                        post_url=record["post_url"],
                        job_description=record["job_description"],
                        email_sent_status=record["email_sent_status"]
                    )

                    if appended:
                        extracted_records.append(record)
                        print(f" [+] Found: {recruiter_name} <{recruiter_email}> | Status: {email_sent_status}")

            except Exception as exc:
                print(f"[linkedin_scraper] Scraping error: {exc}")
            finally:
                browser.close()

        print(f"[linkedin_scraper] Completed scraping for '{role}'. Stored {len(extracted_records)} new records.\n")
        return extracted_records


def scrape_candidate_roles(candidate_slug: str, max_posts_per_role: int = 10) -> int:
    """Scrapes all configured search roles for a given candidate."""
    cand_cfg = CANDIDATE_ROLES.get(candidate_slug)
    if not cand_cfg:
        print(f"[linkedin_scraper] Candidate '{candidate_slug}' not found in CANDIDATE_ROLES.")
        return 0

    scraper = LinkedInScraper()
    total_found = 0
    for search_role in cand_cfg.get("search_roles", []):
        results = scraper.scrape_posts_for_role(search_role, candidate_slug, max_posts=max_posts_per_role)
        total_found += len(results)
    return total_found


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_cand = sys.argv[1]
    else:
        target_cand = "safi"
    scrape_candidate_roles(target_cand)
