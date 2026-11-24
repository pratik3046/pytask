"""
AI Resume Customization Engine & Word COM Compilation Pipeline.
Converts PDF to DOCX (via pdf2docx), parses document structure (Professional Summary,
Project Experience, Technical Skills), prompts a Multi-LLM Chain (Gemini -> Groq -> OpenRouter),
rewrites DOCX run-by-run preserving styles, and compiles back to PDF via thread-safe Word COM.
"""

import os
import re
import json
import time
import shutil
import importlib
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import requests
from dotenv import load_dotenv
import docx
from docx.shared import Pt, Inches

from client_config import CLIENTS, CANDIDATE_ROLES, SCRATCH_DIR, JD_KEYWORDS
from lock_util import word_com_lock, kill_hung_word_processes

load_dotenv()

# Multi-LLM Endpoint Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")


class MultiLLMClient:
    """
    Unified LLM Client providing resilient multi-tier fallback:
    Tier 1: Gemini API (gemini-2.0-flash)
    Tier 2: Groq API (llama-3.3-70b-versatile)
    Tier 3: OpenRouter API (meta-llama/llama-3.3-70b-instruct:free)
    Tier 4: Deterministic rule-based keyword injector (fail-safe)
    """

    @staticmethod
    def _call_gemini(prompt: str, system_prompt: str) -> Optional[str]:
        if not GEMINI_API_KEY or "your_" in GEMINI_API_KEY:
            return None
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{prompt}"}]}
            ],
            "generationConfig": {
                "temperature": 0.2,
                "response_mime_type": "application/json"
            }
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return text
            else:
                print(f"[MultiLLMClient] Gemini returned HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as exc:
            print(f"[MultiLLMClient] Gemini call error: {exc}")
        return None

    @staticmethod
    def _call_groq(prompt: str, system_prompt: str) -> Optional[str]:
        if not GROQ_API_KEY or "your_" in GROQ_API_KEY:
            return None
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"}
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            else:
                print(f"[MultiLLMClient] Groq returned HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as exc:
            print(f"[MultiLLMClient] Groq call error: {exc}")
        return None

    @staticmethod
    def _call_openrouter(prompt: str, system_prompt: str) -> Optional[str]:
        if not OPENROUTER_API_KEY or "your_" in OPENROUTER_API_KEY:
            return None
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/PyTask/c2c-outreach",
            "X-Title": "PyTask C2C Outreach System"
        }
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"}
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            else:
                print(f"[MultiLLMClient] OpenRouter returned HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as exc:
            print(f"[MultiLLMClient] OpenRouter call error: {exc}")
        return None

    @classmethod
    def generate_tailored_json(
        cls,
        summary: str,
        paragraphs: List[str],
        skills: str,
        job_description: str,
        target_role: str
    ) -> Dict[str, Any]:
        """
        Executes Multi-LLM chain with strict fallback to produce JSON rewrites:
        {"rewritten_summary": "...", "rewritten_paragraphs": ["..."], "rewritten_skills": "..."}
        """
        system_prompt = (
            "You are an expert Executive Technical Resume Architect specialized in US C2C staffing. "
            "Your objective is to tailor the candidate's resume content to strictly align with the provided "
            "Job Description (JD). You must weave priority JD technologies 2 to 3 times per project experience bullet "
            "while strictly preserving factual work history, employers, titles, and dates. "
            "Return ONLY a valid JSON object matching this schema:\n"
            "{\n"
            '  "rewritten_summary": "Tailored 3-4 sentence professional summary focusing on JD requirements",\n'
            '  "rewritten_paragraphs": ["Tailored bullet 1", "Tailored bullet 2", ...],\n'
            '  "rewritten_skills": "Comma-separated updated skills prioritizing matched JD tech stack"\n'
            "}"
        )

        user_prompt = (
            f"TARGET ROLE: {target_role}\n\n"
            f"JOB DESCRIPTION:\n{job_description[:3000]}\n\n"
            f"ORIGINAL SUMMARY:\n{summary}\n\n"
            f"ORIGINAL PROJECT BULLETS ({len(paragraphs)} items):\n"
            f"{json.dumps(paragraphs, indent=2)}\n\n"
            f"ORIGINAL TECHNICAL SKILLS:\n{skills}\n"
        )

        # 1. Primary: Gemini
        raw_res = cls._call_gemini(user_prompt, system_prompt)
        if raw_res:
            try:
                data = json.loads(raw_res)
                if "rewritten_summary" in data:
                    print(" [resume_customizer] Resume tailored using Gemini 2.0 Flash.")
                    return data
            except Exception:
                pass

        # 2. Secondary Fallback: Groq
        raw_res = cls._call_groq(user_prompt, system_prompt)
        if raw_res:
            try:
                data = json.loads(raw_res)
                if "rewritten_summary" in data:
                    print(" [resume_customizer] Resume tailored using Groq fallback.")
                    return data
            except Exception:
                pass

        # 3. Tertiary Fallback: OpenRouter
        raw_res = cls._call_openrouter(user_prompt, system_prompt)
        if raw_res:
            try:
                data = json.loads(raw_res)
                if "rewritten_summary" in data:
                    print(" [resume_customizer] Resume tailored using OpenRouter fallback.")
                    return data
            except Exception:
                pass

        # 4. Deterministic Rule-Based Fallback
        print(" [resume_customizer] LLM APIs unavailable. Executing deterministic rule-based keyword tailoring...")
        matched_kws = [kw for kw in JD_KEYWORDS if re.search(rf"\b{re.escape(kw)}\b", job_description, re.IGNORECASE)]
        top_kws = matched_kws[:6] if matched_kws else ["Enterprise Cloud Architecture", "Modern Microservices", "CI/CD"]
        kw_phrase = ", ".join(top_kws)

        rewritten_summary = f"{summary.strip()} Core technical expertise aligned with {kw_phrase}."
        rewritten_paragraphs = []
        for i, p in enumerate(paragraphs):
            assigned_kw = top_kws[i % len(top_kws)]
            rewritten_paragraphs.append(f"{p.strip()} Leveraged {assigned_kw} to ensure high performance and scalability.")
        
        rewritten_skills = f"{skills.strip()}, {kw_phrase}"

        return {
            "rewritten_summary": rewritten_summary,
            "rewritten_paragraphs": rewritten_paragraphs,
            "rewritten_skills": rewritten_skills
        }


# ==============================================================================
# Document Parsing & XML Style-Preserving Helpers
# ==============================================================================
def convert_pdf_to_docx_if_needed(source_path: Path, output_docx_path: Path) -> Path:
    """
    Converts source PDF to DOCX via pdf2docx if the template is a PDF.
    If already DOCX, copies it directly to output_docx_path.
    """
    if str(source_path).lower().endswith(".docx"):
        shutil.copy2(source_path, output_docx_path)
        return output_docx_path

    if str(source_path).lower().endswith(".pdf"):
        try:
            from pdf2docx import Converter
            cv = Converter(str(source_path))
            cv.convert(str(output_docx_path), start=0, end=None)
            cv.close()
            return output_docx_path
        except Exception as exc:
            print(f"[resume_customizer] pdf2docx conversion failed: {exc}. Creating fallback docx.")
            # Fallback: extract text via pypdf and create docx
            import pypdf
            reader = pypdf.PdfReader(str(source_path))
            doc = docx.Document()
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    for line in text.splitlines():
                        if line.strip():
                            doc.add_paragraph(line.strip())
            doc.save(str(output_docx_path))
            return output_docx_path

    shutil.copy2(source_path, output_docx_path)
    return output_docx_path


def extract_sections_from_docx(doc: docx.Document) -> Tuple[str, List[str], str]:
    """
    Extracts Professional Summary, Project Experience bullet paragraphs,
    and Technical Skills from a docx Document.
    """
    summary_lines: List[str] = []
    project_paragraphs: List[str] = []
    skills_lines: List[str] = []

    current_section = "HEADER"

    for p in doc.paragraphs:
        txt = p.text.strip()
        if not txt:
            continue

        upper_txt = txt.upper()
        if any(h in upper_txt for h in ["PROFESSIONAL SUMMARY", "SUMMARY", "PROFILE"]):
            current_section = "SUMMARY"
            continue
        elif any(h in upper_txt for h in ["TECHNICAL SKILLS", "SKILLS", "CORE COMPETENCIES"]):
            current_section = "SKILLS"
            continue
        elif any(h in upper_txt for h in ["PROFESSIONAL EXPERIENCE", "WORK EXPERIENCE", "PROJECTS", "EXPERIENCE"]):
            current_section = "EXPERIENCE"
            continue
        elif any(h in upper_txt for h in ["EDUCATION", "CERTIFICATIONS"]):
            current_section = "OTHER"
            continue

        if current_section == "SUMMARY":
            summary_lines.append(txt)
        elif current_section == "EXPERIENCE":
            if len(txt) > 25:  # meaningful project bullet
                project_paragraphs.append(txt)
        elif current_section == "SKILLS":
            skills_lines.append(txt)

    # Also check tables for skills
    if not skills_lines:
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    ctext = cell.text.strip()
                    if ctext and len(ctext) > 5:
                        skills_lines.append(ctext)

    summary = " ".join(summary_lines) if summary_lines else "Experienced senior software engineer with strong technical track record."
    skills = " | ".join(skills_lines) if skills_lines else "Java, Python, Cloud, Microservices, CI/CD, SQL"
    if not project_paragraphs:
        project_paragraphs = [
            "Designed and implemented high-volume microservices architecture ensuring high availability and fault tolerance.",
            "Engineered automated CI/CD pipelines reducing deployment cycle time and streamlining cloud infrastructure provisioning."
        ]

    return summary, project_paragraphs, skills


def update_paragraph_runs_preserve_style(paragraph: docx.text.paragraph.Paragraph, new_text: str):
    """
    Overwrites the text of a paragraph run-by-run so that the original run formatting
    (font name, size, bold, italic, underline, color) is strictly preserved.
    """
    runs = paragraph.runs
    if not runs:
        # Fallback if no runs exist
        paragraph.text = new_text
        return

    # Keep formatting of the first run
    first_run = runs[0]
    font_name = first_run.font.name
    font_size = first_run.font.size
    bold = first_run.bold
    italic = first_run.italic
    color = first_run.font.color.rgb if first_run.font.color else None

    # Clear all runs
    for run in runs:
        run.text = ""

    # Populate first run with the new text
    first_run.text = new_text
    if font_name:
        first_run.font.name = font_name
    if font_size:
        first_run.font.size = font_size
    first_run.bold = bold
    first_run.italic = italic
    if color:
        first_run.font.color.rgb = color


def docx_to_pdf_pymupdf(docx_path: Path, pdf_path: Path) -> bool:
    """
    High-fidelity PDF compilation fallback using PyMuPDF.
    Parses document paragraphs and tables, rendering formatted typography,
    margins, and layout directly to PDF without requiring Word COM.
    """
    try:
        import pymupdf
        doc = docx.Document(str(docx_path))
        pdf = pymupdf.open()

        page_width, page_height = 612, 792  # Standard Letter
        margin_x, margin_y = 50, 50
        page = pdf.new_page(width=page_width, height=page_height)
        y = margin_y

        for p in doc.paragraphs:
            txt = p.text.strip()
            if not txt:
                y += 6
                continue

            fontsize = 10
            fontname = "helv"
            color = (0.1, 0.1, 0.1)

            # Detect headers and titles
            if p.style.name.startswith("Heading") or (txt.isupper() and len(txt) < 45):
                fontsize = 12
                fontname = "hebo"
                color = (0.05, 0.15, 0.35)
                y += 10
            elif txt.startswith("•") or txt.startswith("-"):
                fontsize = 9.5
                y += 2

            if y > page_height - margin_y - 20:
                page = pdf.new_page(width=page_width, height=page_height)
                y = margin_y

            rect = pymupdf.Rect(margin_x, y, page_width - margin_x, y + 250)
            page.insert_textbox(rect, txt, fontsize=fontsize, fontname=fontname, color=color)
            lines_count = max(1, len(txt) // 85 + 1)
            y += lines_count * (fontsize + 3) + 4

        # Render tables if present
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    if y > page_height - margin_y - 20:
                        page = pdf.new_page(width=page_width, height=page_height)
                        y = margin_y
                    rect = pymupdf.Rect(margin_x + 10, y, page_width - margin_x, y + 60)
                    page.insert_textbox(rect, row_text, fontsize=9.0, fontname="helv", color=(0.15, 0.15, 0.15))
                    y += 18

        pdf.save(str(pdf_path))
        pdf.close()
        return pdf_path.exists() and pdf_path.stat().st_size > 0
    except Exception as exc:
        print(f"[resume_customizer] PyMuPDF PDF fallback error: {exc}")
        return False


def is_word_com_available() -> bool:
    """Checks if Microsoft Word COM automation is accessible and responsive."""
    if os.name != "nt":
        return False
    try:
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.Quit()
        pythoncom.CoUninitialize()
        return True
    except Exception:
        return False


def compile_docx_to_pdf_word_com(docx_path: Path, pdf_path: Path) -> bool:
    """
    Compiles DOCX to PDF using Microsoft Word COM (docx2pdf) under a thread-safe mutex lock.
    If Word COM is unavailable, disabled, or encounters permission boundaries (e.g. Access Denied),
    seamlessly falls back to PyMuPDF compilation.
    """
    # 1. Attempt Word COM if available and responsive
    if is_word_com_available():
        try:
            with word_com_lock(timeout=25.0):
                try:
                    import pythoncom
                    pythoncom.CoInitialize()
                except Exception:
                    pass

                from docx2pdf import convert
                convert(str(docx_path), str(pdf_path))

                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                print(f"[resume_customizer] Compiled via Microsoft Word COM: {pdf_path}")
                return True
        except Exception as exc:
            print(f"[resume_customizer] Word COM PDF compilation error ({exc}). Engaging PyMuPDF engine...")
            kill_hung_word_processes()
    else:
        print("[resume_customizer] Word COM automation inactive/restricted. Using PyMuPDF compilation engine...")

    # 2. PyMuPDF Fallback
    success = docx_to_pdf_pymupdf(docx_path, pdf_path)
    if success:
        print(f"[resume_customizer] Compiled via PyMuPDF engine: {pdf_path}")
        return True

    return pdf_path.exists() and pdf_path.stat().st_size > 0


# ==============================================================================
# Main Customizer Entry Point
# ==============================================================================
def customize_resume(
    candidate_slug: str,
    target_role: str,
    job_description: str,
    output_pdf_path: Optional[Path] = None
) -> Path:
    """
    Orchestrates candidate resume tailoring:
    1. Checks for client-specific customizer module (safi_customizer / tanuja_customizer)
    2. Resolves template path (.docx or .pdf)
    3. Runs section extraction and LLM tailoring
    4. Rewrites DOCX runs preserving styles
    5. Compiles to PDF via thread-safe Word COM
    """
    cand_cfg = CLIENTS.get(candidate_slug)
    if not cand_cfg:
        raise ValueError(f"Candidate slug '{candidate_slug}' not found in CLIENTS config.")

    role_meta = CANDIDATE_ROLES.get(candidate_slug, {})
    customizer_module_name = role_meta.get("customizer_module")

    # Delegate to specialized customizer if defined
    if customizer_module_name:
        try:
            mod = importlib.import_module(customizer_module_name)
            if hasattr(mod, "customize_client_resume"):
                print(f"[resume_customizer] Delegating to specialized hook: {customizer_module_name}.py")
                return mod.customize_client_resume(candidate_slug, target_role, job_description, output_pdf_path)
        except Exception as exc:
            print(f"[resume_customizer] Customizer hook '{customizer_module_name}' error: {exc}. Proceeding with core customizer.")

    # Locate source resume template
    resume_templates = cand_cfg.get("resume_candidates", [])
    source_template: Optional[Path] = None
    for tmpl in resume_templates:
        p = Path(tmpl)
        if p.exists() and p.stat().st_size > 0:
            source_template = p
            break

    if not source_template:
        raise FileNotFoundError(
            f"No valid resume template found for candidate '{candidate_slug}' in {resume_templates}"
        )

    # Prepare working files in scratch/
    timestamp = int(time.time())
    work_docx = SCRATCH_DIR / f"{candidate_slug}_customized_{timestamp}.docx"
    if not output_pdf_path:
        output_pdf_path = SCRATCH_DIR / f"{candidate_slug}_customized_{timestamp}.pdf"

    # Step 1: Ensure we have a workable DOCX
    convert_pdf_to_docx_if_needed(source_template, work_docx)

    # Step 2: Parse sections
    doc = docx.Document(str(work_docx))
    summary, project_paragraphs, skills = extract_sections_from_docx(doc)

    # Step 3: Call Multi-LLM Chain
    tailored_data = MultiLLMClient.generate_tailored_json(
        summary=summary,
        paragraphs=project_paragraphs,
        skills=skills,
        job_description=job_description,
        target_role=target_role
    )

    rewritten_summary = tailored_data.get("rewritten_summary", summary)
    rewritten_bullets = tailored_data.get("rewritten_paragraphs", project_paragraphs)
    rewritten_skills = tailored_data.get("rewritten_skills", skills)

    # Step 4: Style-Preserving XML Rewrite
    current_section = "HEADER"
    bullet_idx = 0

    for p in doc.paragraphs:
        txt = p.text.strip()
        if not txt:
            continue

        upper_txt = txt.upper()
        if any(h in upper_txt for h in ["PROFESSIONAL SUMMARY", "SUMMARY", "PROFILE"]):
            current_section = "SUMMARY"
            continue
        elif any(h in upper_txt for h in ["TECHNICAL SKILLS", "SKILLS", "CORE COMPETENCIES"]):
            current_section = "SKILLS"
            continue
        elif any(h in upper_txt for h in ["PROFESSIONAL EXPERIENCE", "WORK EXPERIENCE", "PROJECTS", "EXPERIENCE"]):
            current_section = "EXPERIENCE"
            continue
        elif any(h in upper_txt for h in ["EDUCATION", "CERTIFICATIONS"]):
            current_section = "OTHER"
            continue

        if current_section == "SUMMARY":
            update_paragraph_runs_preserve_style(p, rewritten_summary)
            current_section = "IN_SUMMARY_DONE"  # Only replace first summary block
        elif current_section == "EXPERIENCE":
            if len(txt) > 25 and bullet_idx < len(rewritten_bullets):
                update_paragraph_runs_preserve_style(p, rewritten_bullets[bullet_idx])
                bullet_idx += 1
        elif current_section == "SKILLS":
            update_paragraph_runs_preserve_style(p, rewritten_skills)
            current_section = "IN_SKILLS_DONE"

    # Clean consecutive blank paragraphs
    clean_paragraphs = []
    prev_blank = False
    for p in doc.paragraphs:
        if not p.text.strip():
            if prev_blank:
                # Remove consecutive blank paragraph element from XML
                p._element.getparent().remove(p._element)
            prev_blank = True
        else:
            prev_blank = False

    doc.save(str(work_docx))

    # Step 5: Thread-Safe Windows COM PDF Compilation
    success = compile_docx_to_pdf_word_com(work_docx, output_pdf_path)
    if not success or not output_pdf_path.exists():
        # Fallback copy docx as reference or attempt direct save
        print(f"[resume_customizer] Word COM PDF compilation produced fallback at {work_docx}")
        return work_docx

    print(f"[resume_customizer] Successfully generated customized resume: {output_pdf_path}")
    return output_pdf_path
