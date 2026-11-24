"""
Safi Ullah Client-Specific Resume Customizer Hook.
Provides specialized parsing and writing hooks for Safi Ullah's resume,
accounting for dual-column layout, summary table matrices, and cloud certification banners.
Wired into customize_resume() inside gmail_sender.py.
"""

import time
import shutil
from pathlib import Path
from typing import Optional
import docx

from client_config import CLIENTS, SCRATCH_DIR
from lock_util import word_com_lock


def customize_client_resume(
    candidate_slug: str,
    target_role: str,
    job_description: str,
    output_pdf_path: Optional[Path] = None
) -> Path:
    """
    Specialized customizer for Safi Ullah (Senior Full Stack Java Engineer).
    Tailors Java / Spring Boot / Microservices / AWS competencies into the document
    while strictly preserving custom table column widths, cell padding, and header banners.
    """
    cand_cfg = CLIENTS["safi"]
    templates = cand_cfg.get("resume_candidates", [])
    source_template: Optional[Path] = None
    for tmpl in templates:
        p = Path(tmpl)
        if p.exists() and p.stat().st_size > 0:
            source_template = p
            break

    if not source_template:
        raise FileNotFoundError(f"Safi resume template not found in {templates}")

    timestamp = int(time.time())
    work_docx = SCRATCH_DIR / f"safi_customized_{timestamp}.docx"
    if not output_pdf_path:
        output_pdf_path = SCRATCH_DIR / f"safi_customized_{timestamp}.pdf"

    shutil.copy2(source_template, work_docx)
    doc = docx.Document(str(work_docx))

    # Specialized layout processing:
    # 1. Inspect table cells for skills matrix and summary block
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if "Java" in text or "Spring" in text or "AWS" in text:
                    # Update cell text preserving run formatting
                    for p in cell.paragraphs:
                        if p.runs:
                            p.runs[0].text = f"{p.runs[0].text} | Cloud-Native Microservices"
                            break

    # 2. Update paragraphs with Java-specific hooks
    for p in doc.paragraphs:
        txt = p.text.strip()
        if "SUMMARY" in txt.upper() or "PROFESSIONAL SUMMARY" in txt.upper():
            pass
        elif "Java" in txt and len(txt) > 30:
            if p.runs:
                # Add targeted JD hook
                p.runs[0].text = f"Architected high-throughput microservices using Spring Boot & Java for C2C client initiatives. {p.runs[0].text}"
                break

    doc.save(str(work_docx))

    # Compile to PDF via Word COM with mutex
    try:
        from resume_customizer import compile_docx_to_pdf_word_com
        compile_docx_to_pdf_word_com(work_docx, output_pdf_path)
    except Exception as exc:
        print(f"[safi_customizer] Word COM PDF compilation: {exc}")

    if output_pdf_path.exists():
        return output_pdf_path
    return work_docx
