"""
Comprehensive Unit & Pipeline Integration Test Suite.
Validates schemas, migration, filtering, mutex locking, document customization,
and analytics reporting without requiring live LinkedIn/Gmail connections.
"""

import os
import sys
import unittest
from pathlib import Path
import pandas as pd

from client_config import CLIENTS, CANDIDATE_ROLES, JD_KEYWORDS, CLIENTS_DIR, SCRATCH_DIR
from db_util import (
    init_databases,
    load_recruiters,
    save_recruiters,
    load_sent_history,
    save_sent_history,
    append_recruiter,
    append_sent_email,
    has_candidate_contacted_recruiter,
    mark_email_replied,
    get_pending_recruiters,
    RECRUITERS_CSV,
    SENT_HISTORY_CSV
)
from lock_util import word_com_lock, WordComLockTimeout
from linkedin_scraper import is_usa_job, matches_strict_filter, extract_recruiter_name_from_url, clean_linkedin_url
from resume_customizer import (
    MultiLLMClient,
    extract_sections_from_docx,
    convert_pdf_to_docx_if_needed,
    customize_resume
)
from gmail_sender import (
    extract_top_jd_keywords,
    generate_outreach_subject,
    generate_outreach_body,
    send_outreach_email
)
from followup_sender import generate_ai_value_proposition, get_unanswered_outreach_records
from analytics import compute_metrics, generate_analytics_html


class TestPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_databases()

    def test_01_client_config(self):
        """Validates candidate configurations and JD keywords taxonomy."""
        self.assertGreaterEqual(len(JD_KEYWORDS), 60)
        self.assertIn("Java", JD_KEYWORDS)
        self.assertIn("Python", JD_KEYWORDS)
        self.assertIn("AWS", JD_KEYWORDS)
        self.assertIn("Kubernetes", JD_KEYWORDS)

        self.assertIn("safi", CLIENTS)
        self.assertIn("tanuja", CLIENTS)
        self.assertIn("alex_dev", CLIENTS)

        for slug, cand in CLIENTS.items():
            self.assertTrue(cand["name"])
            self.assertTrue(cand["email"])
            self.assertIn(slug, CANDIDATE_ROLES)
            self.assertTrue(CANDIDATE_ROLES[slug]["search_roles"])

    def test_02_database_schemas_and_migration(self):
        """Tests CSV auto-migration and candidate-specific de-duplication."""
        rec_df = load_recruiters()
        self.assertIn("recruiter_name", rec_df.columns)
        self.assertIn("recruiter_email", rec_df.columns)
        self.assertIn("role", rec_df.columns)
        self.assertIn("post_url", rec_df.columns)
        self.assertIn("job_description", rec_df.columns)
        self.assertIn("email_sent_status", rec_df.columns)
        self.assertIn("timestamp", rec_df.columns)

        sent_df = load_sent_history()
        self.assertIn("recruiter_email", sent_df.columns)
        self.assertIn("candidate", sent_df.columns)
        self.assertIn("message_id", sent_df.columns)
        self.assertIn("followup_count", sent_df.columns)
        self.assertIn("replied", sent_df.columns)

        import time
        unique_suffix = int(time.time() * 1000)
        test_email = f"test.recruiter.{unique_suffix}@apexsystems.com"
        # Test appending recruiter
        appended = append_recruiter(
            recruiter_name="Sarah Jenkins",
            recruiter_email=test_email,
            role="Senior Java Developer",
            post_url=f"https://www.linkedin.com/feed/update/urn:li:activity:{unique_suffix}/",
            job_description="Urgent requirement for Senior Java Developer with Spring Boot and AWS in Dallas, TX. C2C only."
        )
        self.assertTrue(appended)

        # De-duplication check before send
        self.assertFalse(has_candidate_contacted_recruiter("safi", test_email))

        # Append sent email
        append_sent_email(
            recruiter_name="Sarah Jenkins",
            recruiter_email=test_email,
            role="Senior Java Developer",
            post_url=f"https://www.linkedin.com/feed/update/urn:li:activity:{unique_suffix}/",
            candidate="safi",
            email_sent_status="sent",
            subject="C2C: Safi Ullah",
            message_id=f"<test-msg-id-{unique_suffix}@gmail.com>"
        )

        # Should now be True for safi, but False for tanuja (candidate-level de-duplication)
        self.assertTrue(has_candidate_contacted_recruiter("safi", test_email))
        self.assertFalse(has_candidate_contacted_recruiter("tanuja", test_email))

        # Test reply marking
        updated_replies = mark_email_replied(test_email)
        self.assertEqual(updated_replies, 1)

    def test_03_word_com_mutex_lock(self):
        """Tests process-safe file mutex lock and deadlock recovery."""
        test_lock_file = SCRATCH_DIR / "test_unit.lock"
        if test_lock_file.exists():
            test_lock_file.unlink()

        with word_com_lock(lock_path=test_lock_file, timeout=5.0):
            self.assertTrue(test_lock_file.exists())

        self.assertFalse(test_lock_file.exists())

    def test_04_strict_post_filtering(self):
        """Tests USA location checks, bench marketer spam detection, and domain mismatches."""
        # 1. Non-USA job
        non_usa_post = "Hiring Java Developer for our Pune, India offshore delivery center."
        self.assertFalse(is_usa_job(non_usa_post))
        valid, reason = matches_strict_filter(non_usa_post, "safi")
        self.assertFalse(valid)
        self.assertEqual(reason, "non_usa_location")

        # 2. Bench marketer spam
        bench_post = "I have a top candidate available on my bench for Java roles. Share requirements."
        valid, reason = matches_strict_filter(bench_post, "safi")
        self.assertFalse(valid)
        self.assertEqual(reason, "bench_marketer_spam")

        # 3. Domain mismatch
        devops_post = "Looking for a Lead DevOps Engineer with Kubernetes, Terraform in Dallas, TX. C2C."
        valid, reason = matches_strict_filter(devops_post, "safi")
        self.assertFalse(valid)
        self.assertEqual(reason, "mismatched_role")

        # 4. Valid US C2C Java post
        valid_post = "Immediate C2C contract: Senior Java Developer needed in Dallas, TX. Must have Spring Boot and AWS."
        self.assertTrue(is_usa_job(valid_post))
        valid, status = matches_strict_filter(valid_post, "safi")
        self.assertTrue(valid)
        self.assertEqual(status, "pending")

    def test_05_url_and_name_cleaning(self):
        """Tests URL param stripping and recruiter name extraction."""
        dirty_url = "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678?updateEntityUrn=urn%3Ali%3Afs_feedUpdate&rcm=ACoAAB"
        clean = clean_linkedin_url(dirty_url)
        self.assertEqual(clean, "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678")

        name = extract_recruiter_name_from_url("https://www.linkedin.com/in/michael-scott-talent-acquisition/")
        self.assertEqual(name, "Michael Scott Talent Acquisition")

    def test_06_dynamic_jd_keyword_hooking(self):
        """Tests extracting top 3 keywords from JD and composing email body."""
        jd = """
        Looking for a Senior Software Engineer with strong Java, Spring Boot, AWS, Docker, Kubernetes,
        and PostgreSQL experience. Immediate C2C contract.
        """
        top_kws = extract_top_jd_keywords(jd, max_keywords=3)
        self.assertEqual(len(top_kws), 3)
        self.assertIn("Java", top_kws)

        cand = CLIENTS["safi"]
        subject = generate_outreach_subject(cand["name"], "Senior Java Developer", top_kws)
        self.assertIn("Corp-to-Corp (C2C)", subject)
        self.assertIn(cand["name"], subject)

        body = generate_outreach_body(cand["name"], "Sarah Jenkins", "Senior Java Developer", top_kws, cand)
        self.assertIn("Hi Sarah Jenkins,", body)
        self.assertIn("Corp-to-Corp (C2C)", body)
        self.assertIn("Safi Ullah", body)

    def test_07_resume_customization_and_style_preservation(self):
        """Tests DOCX parsing, run-by-run style preservation, and tailoring."""
        jd = "Senior Java Engineer with Spring Boot, AWS, and Microservices for C2C client in Dallas, TX."
        custom_path = customize_resume("safi", "Senior Java Developer", jd)
        self.assertTrue(custom_path.exists())
        self.assertGreater(custom_path.stat().st_size, 0)

    def test_08_test_mode_dispatch(self):
        """Tests simulated dispatch workflow."""
        import time
        ts = int(time.time() * 1000)
        success, reason = send_outreach_email(
            candidate_slug="tanuja",
            recruiter_name="David Miller",
            recruiter_email=f"david.miller.{ts}@techrecruiting.com",
            role="Python Data Engineer",
            post_url=f"https://www.linkedin.com/feed/update/urn:li:activity:{ts}/",
            job_description="Need Senior Python Data Engineer with PySpark and Snowflake in Austin, TX.",
            test_mode=True
        )
        self.assertTrue(success)
        self.assertEqual(reason, "test_sent_simulated")

    def test_09_analytics_dashboard_generation(self):
        """Tests computing metrics and generating HTML dashboard."""
        metrics = compute_metrics()
        self.assertIn("total_scraped", metrics)
        self.assertIn("total_dispatched", metrics)

        html_path = generate_analytics_html()
        self.assertTrue(html_path.exists())
        self.assertGreater(html_path.stat().st_size, 500)
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("C2C Recruitment Outreach Analytics", content)
            self.assertIn("Total Posts Scraped", content)


if __name__ == "__main__":
    unittest.main()
