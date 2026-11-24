"""
LinkedIn Authentication Module.
Launches headful Chromium browser via Playwright to permit operator login,
2FA / CAPTCHA resolution, and persists authenticated session state (cookies and local storage)
to session.json.
"""

import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, BrowserContext, Page

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
SESSION_FILE = BASE_DIR / "session.json"
LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"


def is_session_valid() -> bool:
    """Checks if session.json exists and has valid cookie entries."""
    if not SESSION_FILE.exists() or SESSION_FILE.stat().st_size == 0:
        return False
    try:
        import json
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            cookies = data.get("cookies", [])
            # Must have li_at cookie for valid auth
            for c in cookies:
                if c.get("name") == "li_at":
                    return True
        return False
    except Exception:
        return False


def run_login(timeout_seconds: int = 180) -> bool:
    """
    Launches headful Chromium window for LinkedIn authentication.
    Auto-fills credentials if defined in .env and waits for operator
    to solve any security checks (CAPTCHA / OTP).
    Saves context state to session.json upon successful feed navigation.
    """
    email = os.getenv("LINKEDIN_EMAIL", "")
    password = os.getenv("LINKEDIN_PASSWORD", "")

    print("\n" + "=" * 70)
    print(" [LINKEDIN LOGIN] Launching Headful Chromium Authentication Window...")
    print("=" * 70)
    print(" If prompted with a CAPTCHA or 2FA code, please complete it directly in the browser.")
    print(" The session will automatically save once the LinkedIn feed loads.")
    print("-" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ]
        )
        context: BrowserContext = browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            permissions=["clipboard-read", "clipboard-write"]
        )

        page: Page = context.new_page()

        try:
            page.goto(LINKEDIN_LOGIN_URL, timeout=45000, wait_until="domcontentloaded")
            time.sleep(2)

            # Auto-fill credentials if available
            if email and password:
                print(f"[linkedin_login] Attempting auto-fill for {email}...")
                try:
                    if page.is_visible("#username"):
                        page.fill("#username", email)
                        page.fill("#password", password)
                        time.sleep(1)
                        page.click("button[type='submit']")
                except Exception as exc:
                    print(f"[linkedin_login] Auto-fill note: {exc}")

            print("[linkedin_login] Waiting for authenticated LinkedIn home feed navigation...")
            start_wait = time.time()
            logged_in = False

            while (time.time() - start_wait) < timeout_seconds:
                current_url = page.url
                # Check for security challenge / 2FA
                if "/checkpoint/" in current_url:
                    print("[linkedin_login] Security verification / 2FA detected. Please complete it in the browser...")

                # Feed detection conditions
                if "/feed" in current_url:
                    logged_in = True
                    break

                # Global nav bar or search box selector check
                try:
                    if page.is_visible("#global-nav") or page.is_visible("input[aria-label*='Search']"):
                        logged_in = True
                        break
                except Exception:
                    pass

                time.sleep(2)

            if not logged_in:
                print("[linkedin_login] Login timeout exceeded before reaching feed.")
                browser.close()
                return False

            print("[linkedin_login] Login detected successfully! Capturing session state...")
            time.sleep(3)  # Let cookies settle
            context.storage_state(path=str(SESSION_FILE))
            print(f"[linkedin_login] Session successfully persisted to: {SESSION_FILE}")
            browser.close()
            return True

        except Exception as exc:
            print(f"[linkedin_login] Login process encountered an error: {exc}")
            try:
                browser.close()
            except Exception:
                pass
            return False


if __name__ == "__main__":
    success = run_login()
    sys.exit(0 if success else 1)
