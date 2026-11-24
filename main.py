"""
Interactive CLI Orchestrator & Production Pipeline Controller.
Entry point for the C2C Recruitment & Outreach Automation System.
Provides Rich terminal menus, setup diagnostics, single-candidate workflows,
manual operator approval gating ("SEND"), and automated multi-candidate batch execution.
"""

import os
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box
from dotenv import load_dotenv

from client_config import CLIENTS, CANDIDATE_ROLES, CLIENTS_DIR, SCRATCH_DIR
from db_util import (
    init_databases,
    load_recruiters,
    load_sent_history,
    get_pending_recruiters,
    has_candidate_contacted_recruiter
)
from linkedin_login import is_session_valid, run_login
from linkedin_scraper import scrape_candidate_roles, LinkedInScraper
from gmail_sender import send_outreach_email
from followup_sender import run_followups
from imap_reply_detector import scan_inbox_for_replies
from analytics import generate_analytics_html

load_dotenv()
console = Console()

BANNER_ART = """[bold cyan]
  ██████╗ ██╗   ██╗████████╗ █████╗ ███████╗██╗  ██╗
  ██╔══██╗╚██╗ ██╔╝╚══██╔══╝██╔══██╗██╔════╝██║ ██╔╝
  ██████╔╝ ╚████╔╝    ██║   ███████║███████╗█████╔╝ 
  ██╔═══╝   ╚██╔╝     ██║   ██╔══██║╚════██║██╔═██╗ 
  ██║        ██║      ██║   ██║  ██║███████║██║  ██╗
  ╚═╝        ╚═╝      ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
[/bold cyan][dim]  Client Application Mode • Production C2C Outreach Pipeline[/dim]
"""


def display_banner():
    """Prints system title and environment details."""
    console.print(BANNER_ART)


def validate_environment_setup():
    """Validates local environment, directories, templates, Word COM, and credentials."""
    init_databases()
    console.print("\n[bold yellow]─── System Setup Diagnostics ───[/bold yellow]")

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold magenta")
    table.add_column("Component / Resource", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    # 1. Directory Checks
    clients_exist = CLIENTS_DIR.exists()
    table.add_row(
        "clients/ Directory",
        "[green]OK[/green]" if clients_exist else "[red]MISSING[/red]",
        str(CLIENTS_DIR)
    )

    scratch_exist = SCRATCH_DIR.exists()
    table.add_row(
        "scratch/ Directory",
        "[green]OK[/green]" if scratch_exist else "[red]MISSING[/red]",
        str(SCRATCH_DIR)
    )

    # 2. LinkedIn Session Check
    session_ok = is_session_valid()
    table.add_row(
        "LinkedIn Session (session.json)",
        "[green]AUTHENTICATED[/green]" if session_ok else "[yellow]NOT AUTHENTICATED[/yellow]",
        "Valid 'li_at' cookie found" if session_ok else "Run Option 2 to log in"
    )

    # 3. Gmail Credentials
    gmail_email = os.getenv("GMAIL_EMAIL", "")
    gmail_app_pw = os.getenv("GMAIL_APP_PASSWORD", "")
    gmail_ok = bool(gmail_email and gmail_app_pw and "your_" not in gmail_email)
    table.add_row(
        "Gmail SMTP/IMAP Credentials",
        "[green]CONFIGURED[/green]" if gmail_ok else "[yellow]UNCONFIGURED[/yellow]",
        gmail_email if gmail_ok else "GMAIL_EMAIL / GMAIL_APP_PASSWORD missing in .env"
    )

    # 4. Multi-LLM API Keys
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    groq_key = os.getenv("GROQ_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")

    llm_status = []
    if gemini_key and "your_" not in gemini_key:
        llm_status.append("Gemini (Primary)")
    if groq_key and "your_" not in groq_key:
        llm_status.append("Groq (Secondary)")
    if openrouter_key and "your_" not in openrouter_key:
        llm_status.append("OpenRouter (Tertiary)")

    llm_summary = ", ".join(llm_status) if llm_status else "Deterministic Fallback Active"
    table.add_row(
        "Generative AI Providers",
        "[green]READY[/green]" if llm_status else "[yellow]RULE FALLBACK[/yellow]",
        llm_summary
    )

    # 5. Candidate Template Verifications
    for slug, cdata in CLIENTS.items():
        found = any(Path(p).exists() for p in cdata.get("resume_candidates", []))
        table.add_row(
            f"Candidate Template: {cdata['name']}",
            "[green]FOUND[/green]" if found else "[yellow]TEMPLATE MISSING[/yellow]",
            f"Slug: {slug}"
        )

    console.print(table)


def run_single_candidate_interactive():
    """Runs interactive search, inspection, and operator approval gated dispatch."""
    console.print("\n[bold cyan]Select a Registered Client Candidate:[/bold cyan]")
    candidates = list(CLIENTS.keys())
    for idx, cslug in enumerate(candidates, 1):
        cname = CLIENTS[cslug]["name"]
        crole = CANDIDATE_ROLES[cslug]["primary_role"]
        console.print(f"  [bold]{idx}[/bold]. {cname} ([dim]{crole}[/dim])")

    choice = Prompt.ask("Choose candidate", choices=[str(i) for i in range(1, len(candidates) + 1)])
    selected_slug = candidates[int(choice) - 1]
    cand_info = CLIENTS[selected_slug]

    console.print(f"\n[green]Selected Candidate: {cand_info['name']} ({selected_slug})[/green]")

    # Sub-action
    console.print("  [1] Scrape LinkedIn for Candidate's Target Roles")
    console.print("  [2] Inspect Pending Records & Dispatch Cold Outreach")
    console.print("  [3] Return to Main Menu")
    sub = Prompt.ask("Select action", choices=["1", "2", "3"])

    if sub == "1":
        if not is_session_valid():
            console.print("[bold yellow]No authenticated LinkedIn session found (session.json missing).[/bold yellow]")
            console.print("[dim]LinkedIn requires an authenticated browser session to search recruiter posts.[/dim]")
            if Confirm.ask("[bold cyan]Would you like to launch the LinkedIn login window now to authenticate?[/bold cyan]", default=True):
                login_success = run_login()
                if not login_success:
                    console.print("[bold red]Login was not completed. Cannot proceed with scraping without authentication.[/bold red]")
                    return
            else:
                console.print("[yellow]Scraping aborted. Please run Option 2 to authenticate before scraping.[/yellow]")
                return

        console.print(f"[bold]Executing targeted LinkedIn scraper for {cand_info['name']}...[/bold]")
        found = scrape_candidate_roles(selected_slug, max_posts_per_role=8)
        console.print(f"[bold green]Scrape complete. Identified and stored {found} new recruiter posts.[/bold green]")

    elif sub == "2":
        inspect_and_dispatch_pending(selected_slug)


def inspect_and_dispatch_pending(candidate_slug: Optional[str] = None):
    """
    Displays pending recruiter records in recruiters.csv and enforces explicit
    operator approval ("SEND") before triggering gmail_sender.py.
    """
    rec_df = load_recruiters()
    if rec_df.empty:
        console.print("[yellow]No records found in recruiters.csv.[/yellow]")
        return

    pending = rec_df[rec_df["email_sent_status"] == "pending"]
    if pending.empty:
        console.print("[green]No pending records in recruiters.csv awaiting dispatch.[/green]")
        return

    # If candidate specified, filter by not already contacted
    if candidate_slug:
        cand_name = CLIENTS[candidate_slug]["name"]
        pending = pending[
            ~pending["recruiter_email"].apply(lambda em: has_candidate_contacted_recruiter(candidate_slug, em))
        ]
        console.print(f"\n[bold]Pending Recruiters for Candidate: {cand_name} ({len(pending)} eligible)[/bold]")
    else:
        console.print(f"\n[bold]All Pending Recruiters ({len(pending)} records)[/bold]")

    if pending.empty:
        console.print("[yellow]All available recruiters have already been contacted by this candidate.[/yellow]")
        return

    # Render Table
    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Recruiter Name", style="bold")
    table.add_column("Recruiter Email", style="yellow")
    table.add_column("Target Role", style="green")
    table.add_column("Post URL", style="dim", max_width=40)

    for idx, (_, row) in enumerate(pending.head(15).iterrows(), 1):
        table.add_row(
            str(idx),
            str(row["recruiter_name"]),
            str(row["recruiter_email"]),
            str(row["role"]),
            str(row["post_url"])
        )

    console.print(table)

    # Manual Approval Gating
    console.print("\n[bold red]ATTENTION: Manual Operator Approval Required[/bold red]")
    console.print("To dispatch personalized outreach emails with customized PDF resumes,")
    approval = Prompt.ask("[bold yellow]Type 'SEND' to confirm dispatch, or any other key to abort[/bold yellow]")

    if approval.strip() != "SEND":
        console.print("[bold red]Dispatch aborted by operator. No emails were sent.[/bold red]")
        return

    # Execution Mode
    mode = Prompt.ask("Dispatch Mode", choices=["Live", "Test"], default="Live")
    is_test = (mode == "Test")

    # Send up to cap
    target_cand = candidate_slug if candidate_slug else list(CLIENTS.keys())[0]
    sent_count = 0
    fail_count = 0

    console.print(f"\n[bold green]Dispatching cold outreach ({mode} Mode)...[/bold green]")
    for _, row in pending.iterrows():
        success, reason = send_outreach_email(
            candidate_slug=target_cand,
            recruiter_name=str(row["recruiter_name"]),
            recruiter_email=str(row["recruiter_email"]),
            role=str(row["role"]),
            post_url=str(row["post_url"]),
            job_description=str(row["job_description"]),
            test_mode=is_test
        )
        if success:
            sent_count += 1
        else:
            fail_count += 1

    console.print(f"\n[bold green]Batch complete. Dispatched: {sent_count} | Failed/Skipped: {fail_count}[/bold green]")


def run_full_pipeline_batch():
    """
    Option 8: Automated Batch Execution for ALL Candidates.
    Prompts for Live/Test mode and daily limit, verifies session once,
    iterates through all configured candidates, scrapes, tailors, and sends.
    """
    console.print(Panel.fit(
        "[bold cyan]Automated Batch Execution (Full 7-Stage Pipeline)[/bold cyan]\n"
        "Iterates sequentially through all registered candidates in client_config.py,\n"
        "scrapes target roles, isolates uncontacted recruiters, customizes resumes,\n"
        "and dispatches emails up to the configured daily limit.",
        box=box.DOUBLE
    ))

    mode_choice = Prompt.ask("Select Dispatch Mode", choices=["Live", "Test"], default="Test")
    is_test = (mode_choice == "Test")

    limit_str = Prompt.ask("Global Daily Email Cap", default="25")
    try:
        daily_limit = int(limit_str)
    except ValueError:
        daily_limit = 25

    console.print(f"[bold yellow]Batch Mode: {mode_choice} | Daily Limit: {daily_limit}[/bold yellow]")

    if not is_session_valid():
        console.print("[bold yellow]No authenticated LinkedIn session found (session.json missing).[/bold yellow]")
        if Confirm.ask("[bold cyan]Would you like to launch the LinkedIn login window now to authenticate?[/bold cyan]", default=True):
            login_success = run_login()
            if not login_success:
                console.print("[bold red]Login was not completed. Batch scraping cannot proceed without authentication.[/bold red]")
                return
        else:
            console.print("[yellow]Batch execution aborted. Please run Option 2 to authenticate.[/yellow]")
            return

    global_sent = 0

    for cand_slug, cand_cfg in CLIENTS.items():
        if global_sent >= daily_limit:
            console.print("[yellow]Global daily limit reached. Stopping batch.[/yellow]")
            break

        console.print(f"\n[bold magenta]═══════════════════════════════════════════════════════[/bold magenta]")
        console.print(f"[bold cyan]Processing Candidate: {cand_cfg['name']} ({cand_slug})[/bold cyan]")
        console.print(f"[bold magenta]═══════════════════════════════════════════════════════[/bold magenta]")

        # 1. Scrape Roles
        console.print(f"[*] Scraping LinkedIn search roles for {cand_slug}...")
        scrape_candidate_roles(cand_slug, max_posts_per_role=5)

        # 2. Filter & Isolate Pending Records
        rec_df = load_recruiters()
        if rec_df.empty:
            continue

        pending = rec_df[rec_df["email_sent_status"] == "pending"]
        # Exclude recruiters contacted by this candidate
        eligible = pending[
            ~pending["recruiter_email"].apply(lambda em: has_candidate_contacted_recruiter(cand_slug, em))
        ]

        console.print(f"[*] Found {len(eligible)} eligible recruiters for {cand_slug}.")

        for _, row in eligible.iterrows():
            if global_sent >= daily_limit:
                break

            console.print(f" -> Contacting: {row['recruiter_name']} <{row['recruiter_email']}> for {row['role']}")
            success, reason = send_outreach_email(
                candidate_slug=cand_slug,
                recruiter_name=str(row["recruiter_name"]),
                recruiter_email=str(row["recruiter_email"]),
                role=str(row["role"]),
                post_url=str(row["post_url"]),
                job_description=str(row["job_description"]),
                test_mode=is_test
            )
            if success:
                global_sent += 1

    console.print(f"\n[bold green]Automated Batch Complete! Total emails dispatched: {global_sent}[/bold green]")
    generate_analytics_html()


def main_menu():
    """Main interactive menu loop."""
    init_databases()

    while True:
        display_banner()
        console.print("[bold]Select an action from the pipeline menu:[/bold]\n")
        console.print("  [bold cyan]1[/bold cyan]. Validate Environment & System Health")
        console.print("  [bold cyan]2[/bold cyan]. Setup / Test LinkedIn Login Session (Headful Chromium)")
        console.print("  [bold cyan]3[/bold cyan]. Run Targeted LinkedIn Scraper (Single Candidate)")
        console.print("  [bold cyan]4[/bold cyan]. Inspect Pending Recruiters & Manual Approval Dispatch ('SEND')")
        console.print("  [bold cyan]5[/bold cyan]. Dispatch Threaded Follow-Up Emails (7-Day Unanswered)")
        console.print("  [bold cyan]6[/bold cyan]. Synchronize Inbox Replies via IMAP (replied = 1)")
        console.print("  [bold cyan]7[/bold cyan]. Generate & View Analytics Dashboard (analytics_report.html)")
        console.print("  [bold cyan]8[/bold cyan]. [bold green]Run Full Pipeline for ALL Candidates (Automated Batch Mode)[/bold green]")
        console.print("  [bold cyan]9[/bold cyan]. Exit System\n")

        choice = Prompt.ask("Select an option [1-9]", choices=[str(i) for i in range(1, 10)])

        if choice == "1":
            validate_environment_setup()
            Prompt.ask("\nPress Enter to return to main menu")

        elif choice == "2":
            run_login()
            Prompt.ask("\nPress Enter to return to main menu")

        elif choice == "3":
            run_single_candidate_interactive()
            Prompt.ask("\nPress Enter to return to main menu")

        elif choice == "4":
            inspect_and_dispatch_pending()
            Prompt.ask("\nPress Enter to return to main menu")

        elif choice == "5":
            days = Prompt.ask("Follow-up threshold in days", default="7")
            try:
                days_int = int(days)
            except ValueError:
                days_int = 7
            mode = Prompt.ask("Mode", choices=["Live", "Test"], default="Live")
            run_followups(days_threshold=days_int, test_mode=(mode == "Test"))
            Prompt.ask("\nPress Enter to return to main menu")

        elif choice == "6":
            scan_inbox_for_replies()
            Prompt.ask("\nPress Enter to return to main menu")

        elif choice == "7":
            report_path = generate_analytics_html()
            console.print(f"[bold green]Analytics HTML Dashboard generated successfully:[/bold green] {report_path}")
            Prompt.ask("\nPress Enter to return to main menu")

        elif choice == "8":
            run_full_pipeline_batch()
            Prompt.ask("\nPress Enter to return to main menu")

        elif choice == "9":
            console.print("[bold yellow]Exiting PyTask Outreach Automation. Goodbye![/bold yellow]")
            sys.exit(0)


if __name__ == "__main__":
    main_menu()
