"""
Tanuja Patel Client-Specific Resume Customizer Hook.
Provides specialized parsing and writing hooks for Tanuja Patel's resume,
accounting for custom Data Pipeline tables, Python/AI project matrices, and margins.
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
    Specialized customizer for Tanuja Patel (Senior Python / Data / AI Engineer).
    Tailors Python / Data Pipelines / PySpark / LLM competencies into the document
    while strictly preserving custom table column widths, cell padding, and header banners.
    """
    cand_cfg = CLIENTS["tanuja"]
    templates = cand_cfg.get("resume_candidates", [])
    source_template: Optional[Path] = None
    for tmpl in templates:
        p = Path(tmpl)
        if p.exists() and p.stat().st_size > 0:
            source_template = p
            break

    if not source_template:
        raise FileNotFoundError(f"Tanuja resume template not found in {templates}")

    timestamp = int(time.time())
    work_docx = SCRATCH_DIR / f"tanuja_customized_{timestamp}.docx"
    if not output_pdf_path:
        output_pdf_path = SCRATCH_DIR / f"tanuja_customized_{timestamp}.pdf"

    shutil.copy2(source_template, work_docx)
    doc = docx.Document(str(work_docx))

    # Specialized layout processing for Tanuja's data engineering matrices
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if "Python" in text or "SQL" in text or "Spark" in text:
                    for p in cell.paragraphs:
                        if p.runs:
                            p.runs[0].text = f"{p.runs[0].text} | Real-Time Data Streaming & Distributed Systems"
                            break

    # Paragraph hooks
    for p in doc.paragraphs:
        txt = p.text.strip()
        if "Python" in txt and len(txt) > 30:
            if p.runs:
                p.runs[0].text = f"Built scalable data ingestion pipelines and AI models using Python and PySpark. {p.runs[0].text}"
                break

    doc.save(str(work_docx))

    # Compile to PDF via Word COM with mutex
    try:
        from resume_customizer import compile_docx_to_pdf_word_com
        compile_docx_to_pdf_word_com(work_docx, output_pdf_path)
    except Exception as exc:
        print(f"[tanuja_customizer] Word COM PDF compilation: {exc}")

    if output_pdf_path.exists():
        return output_pdf_path
    return work_docx
