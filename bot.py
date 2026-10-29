#!/usr/bin/env python3
"""
resume-bot · bot.py
====================
A Telegram bot that:
  1. Stores your resume as Markdown (set once via /setresume, persisted to disk).
  2. Accepts a job description from an authorised user.
  3. Uses Google Gemini to tailor the Summary, Skills, and Experience sections
     from the stored Markdown to match the JD.
  4. Python-parses the static sections (Heading, Education) directly from the MD.
  5. Compiles the assembled LaTeX source twice with pdflatex.
  6. Returns the finished PDF as a Telegram document.

Resume Markdown format  (see resume_data.md.example):
──────────────────────────────────────────────────────
# Full Name

## Contact
- phone: …
- email: …
- website: …           (with or without https://)
- linkedin: …          (URL or linkedin.com/in/handle)

## Summary
2–3 sentence prose paragraph.

## Experience

### Job Title | Date Range | Company Name | Location
- Bullet point
- Another bullet

(repeat for each role)

## Skills
- Category Name: item1, item2, item3

(repeat for each category)

## Education

### Institution Name | Location | Degree Title | Date Range

(repeat for each entry)
──────────────────────────────────────────────────────

Required environment variables (see .env.example):
  TELEGRAM_BOT_TOKEN        – token from @BotFather
  TELEGRAM_ALLOWED_USER_ID  – numeric Telegram user ID allowed to use the bot
  GEMINI_API_KEY            – Google AI Studio API key
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Environment ───────────────────────────────────────────────────────────────
load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID: int = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
MODEL_ID: str = "gemini-2.5-flash"

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
TEMPLATE_PATH = BASE_DIR / "resume_template.tex"
DATA_DIR = BASE_DIR / "data"
RESUME_DATA_PATH = DATA_DIR / "resume_data.md"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Gemini client ─────────────────────────────────────────────────────────────
genai_client = genai.Client(api_key=GEMINI_API_KEY)

# ── Conversation states ───────────────────────────────────────────────────────
AWAITING_RESUME = 0


# ─────────────────────────────────────────────────────────────────────────────
# Resume data persistence
# ─────────────────────────────────────────────────────────────────────────────


def load_resume_data() -> str | None:
    """Return stored resume Markdown, or None if not yet set."""
    if RESUME_DATA_PATH.exists():
        content = RESUME_DATA_PATH.read_text(encoding="utf-8").strip()
        return content or None
    return None


def save_resume_data(content: str) -> None:
    """Persist resume Markdown to disk."""
    RESUME_DATA_PATH.write_text(content.strip(), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Markdown → LaTeX helpers  (static sections — no LLM needed)
# ─────────────────────────────────────────────────────────────────────────────


def _latex_escape(text: str) -> str:
    """Escape LaTeX special characters in plain text."""
    for char, escaped in [
        ("&",  r"\&"),
        ("%",  r"\%"),
        ("$",  r"\$"),
        ("#",  r"\#"),
        ("_",  r"\_"),
    ]:
        text = text.replace(char, escaped)
    return text


def _section_body(md: str, heading: str) -> str:
    """
    Return the text body of a Markdown ## section (up to the next ## or EOF).
    `heading` is matched case-insensitively.
    """
    pattern = rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=\n^##\s|\Z)"
    m = re.search(pattern, md, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def build_heading_latex(md: str) -> str:
    """
    Parse `# Name` and `## Contact` from the Markdown.
    Returns a LaTeX \\begin{center}…\\end{center} block.
    """
    lines = md.splitlines()

    # Name – first H1 line
    name = ""
    for line in lines:
        if line.startswith("# "):
            name = _latex_escape(line[2:].strip())
            break

    # Contact key-value pairs
    contacts: dict[str, str] = {}
    in_contact = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^##\s+Contact\s*$", stripped, re.IGNORECASE):
            in_contact = True
            continue
        if in_contact:
            if stripped.startswith("##"):
                break
            m = re.match(r"^[-*]\s+([\w\s]+):\s*(.+)", stripped)
            if m:
                contacts[m.group(1).strip().lower()] = m.group(2).strip()

    # Build contact parts
    parts: list[str] = []

    if "phone" in contacts:
        parts.append(_latex_escape(contacts["phone"]))

    if "email" in contacts:
        e = contacts["email"]
        parts.append(
            rf"\href{{mailto:{e}}}{{\underline{{{_latex_escape(e)}}}}}"
        )

    if "website" in contacts:
        raw = contacts["website"]
        full = raw if raw.startswith("http") else f"https://{raw}"
        display = re.sub(r"^https?://", "", raw)
        parts.append(
            rf"\href{{{full}}}{{\underline{{{_latex_escape(display)}}}}}"
        )

    if "linkedin" in contacts:
        raw = contacts["linkedin"]
        full = raw if raw.startswith("http") else f"https://{raw}"
        display = re.sub(r"^https?://", "", raw)
        parts.append(
            rf"\href{{{full}}}{{\underline{{{_latex_escape(display)}}}}}"
        )

    contact_line = r" $|$ ".join(parts)

    return (
        r"\begin{center}" + "\n"
        rf"    \textbf{{\Huge \scshape {name}}} \\ \vspace{{1pt}}" + "\n"
        rf"    \small {contact_line}" + "\n"
        r"\end{center}"
    )


def build_education_latex(md: str) -> str:
    """
    Parse `## Education` entries.

    Expected per-entry format (one `###` line):
      ### Institution | Location | Degree Title | Date Range

    Returns \\resumeSubheading entries (no outer list wrapper — template provides it).
    """
    body = _section_body(md, "Education")
    if not body:
        logger.warning("No ## Education section found in resume Markdown.")
        return ""

    entries: list[str] = []
    for m in re.finditer(r"^###\s+(.+)", body, re.MULTILINE):
        parts = [p.strip() for p in m.group(1).split("|")]
        if len(parts) < 4:
            logger.warning(
                "Education entry needs 4 pipe-separated fields, got %d: %s",
                len(parts), m.group(1),
            )
            continue
        institution = _latex_escape(parts[0])
        location    = _latex_escape(parts[1])
        degree      = _latex_escape(parts[2])
        dates       = _latex_escape(parts[3])
        entries.append(
            f"    \\resumeSubheading\n"
            f"      {{{institution}}}{{{location}}}\n"
            f"      {{{degree}}}{{{dates}}}"
        )

    return "\n\n".join(entries)


# ─────────────────────────────────────────────────────────────────────────────
# LLM tailoring  (dynamic sections)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert technical resume writer and LaTeX formatter.

Given a candidate's full resume in Markdown and a target job description,
produce tailored LaTeX content for THREE sections: Summary, Skills, Experience.

The template uses the Jake Gutierrez LaTeX resume style.  Available macros:
  \\resumeSubheading{Title}{Dates}{Company}{Location}
  \\resumeItemListStart  /  \\resumeItemListEnd
  \\resumeItem{bullet text}
  Skills row: \\small{\\item{ \\textbf{Category}{: item1, item2} \\\\ }}

Strict rules:
1. Output ONLY the three delimited blocks. No markdown fences, no commentary.
2. Do NOT emit \\resumeSubHeadingListStart/End or outer \\begin{itemize} —
   the template already wraps each section.
3. Preserve EVERY \\resumeSubheading (title, company, dates, location) verbatim.
   You may reorder or trim bullets WITHIN a role to surface JD relevance.
4. Skills: reorder categories and items to put the most JD-relevant first.
   Never add a skill that is absent from the source resume.
5. ===SUMMARY=== must be 2–3 sentences of plain prose — NO LaTeX markup.
   Tailor it to the role using only facts from the resume.
6. In ===SKILLS=== and ===EXPERIENCE===, escape special chars: \\& \\% \\_ \\$ \\#
7. Never fabricate roles, technologies, companies, dates, or metrics.

Output format — include delimiter lines VERBATIM:

===SUMMARY===
<2-3 sentence plain-text summary tailored to the JD>
===SKILLS===
<skills block using \\small{\\item{...}} rows>
===EXPERIENCE===
<experience using \\resumeSubheading + \\resumeItemListStart/End>
"""


def _build_user_prompt(resume_md: str, job_description: str) -> str:
    return (
        f"RESUME (Markdown):\n{resume_md}\n\n"
        f"JOB DESCRIPTION:\n{job_description}\n\n"
        "Produce the tailored output now."
    )


def tailor_with_llm(
    resume_md: str, job_description: str
) -> tuple[str, str, str]:
    """
    Call Gemini to tailor the resume.

    Returns
    -------
    (summary_text, skills_latex, experience_latex)
        Falls back to extracted MD content on any error.
    """
    try:
        response = genai_client.models.generate_content(
            model=MODEL_ID,
            contents=_build_user_prompt(resume_md, job_description),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=8192,
            ),
        )
        raw = response.text or ""
        return _parse_llm_response(raw, resume_md)
    except Exception as exc:
        logger.error("Gemini API error: %s", exc)
        return _extract_summary_fallback(resume_md), "", ""


def _parse_llm_response(raw: str, resume_md: str) -> tuple[str, str, str]:
    """Extract ===SUMMARY===, ===SKILLS===, ===EXPERIENCE=== from LLM output."""
    summary_m = re.search(
        r"===SUMMARY===(.*?)(?===SKILLS===|===EXPERIENCE===|$)", raw, re.DOTALL
    )
    skills_m = re.search(
        r"===SKILLS===(.*?)(?===EXPERIENCE===|$)", raw, re.DOTALL
    )
    exp_m = re.search(r"===EXPERIENCE===(.*?)$", raw, re.DOTALL)

    summary    = summary_m.group(1).strip() if summary_m else ""
    skills     = skills_m.group(1).strip()  if skills_m  else ""
    experience = exp_m.group(1).strip()     if exp_m     else ""

    missing = [k for k, v in [("summary", summary), ("skills", skills), ("experience", experience)] if not v]
    if missing:
        logger.warning("LLM response missing sections: %s — using fallbacks", missing)
        summary = summary or _extract_summary_fallback(resume_md)

    # The summary is plain text — escape it for LaTeX before insertion
    summary = _latex_escape(summary)

    return summary, skills, experience


def _extract_summary_fallback(resume_md: str) -> str:
    """Pull the raw ## Summary paragraph as a LaTeX-escaped fallback."""
    body = _section_body(resume_md, "Summary")
    return _latex_escape(body) if body else ""


# ─────────────────────────────────────────────────────────────────────────────
# LaTeX compilation
# ─────────────────────────────────────────────────────────────────────────────


def compile_latex(
    heading: str,
    summary: str,
    skills: str,
    experience: str,
    education: str,
) -> tuple[bytes | None, str]:
    """
    Slot all five sections into the template, compile twice with pdflatex,
    and return the raw PDF bytes.

    Returns
    -------
    (pdf_bytes, log_tail)
        pdf_bytes is None on failure; log_tail has the last 1 000 chars of
        the pdflatex log for diagnostics.
    """
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    source = (
        template
        .replace("%HEADING%",            heading)
        .replace("%SUMMARY%",            summary)
        .replace("%EXPERIENCE_SECTION%", experience)
        .replace("%SKILLS_SECTION%",     skills)
        .replace("%EDUCATION_SECTION%",  education)
    )

    pdflatex_cmd = [
        "pdflatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "resume.tex",
    ]

    with tempfile.TemporaryDirectory(prefix="resume_compile_") as tmp_dir:
        tex_path = Path(tmp_dir) / "resume.tex"
        pdf_path = Path(tmp_dir) / "resume.pdf"
        log_path = Path(tmp_dir) / "resume.log"

        tex_path.write_text(source, encoding="utf-8")

        log_tail = ""
        for run_number in range(1, 3):  # two passes for stable cross-refs
            result = subprocess.run(
                pdflatex_cmd,
                cwd=tmp_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            logger.info(
                "pdflatex run %d exit code: %d", run_number, result.returncode
            )
            if result.returncode != 0:
                if log_path.exists():
                    log_full = log_path.read_text(encoding="utf-8", errors="replace")
                    log_tail = log_full[-1000:]
                else:
                    log_tail = result.stderr[-1000:] or result.stdout[-1000:]
                logger.error("pdflatex failed on run %d.\n%s", run_number, log_tail)
                return None, log_tail

        if pdf_path.exists():
            return pdf_path.read_bytes(), ""

        logger.error("pdflatex succeeded but PDF not found at %s", pdf_path)
        return None, "PDF not found after successful compilation."


# ─────────────────────────────────────────────────────────────────────────────
# Telegram handlers
# ─────────────────────────────────────────────────────────────────────────────


def _is_authorised(update: Update) -> bool:
    """Return True only if the message comes from the configured allowed user."""
    return bool(
        update.effective_user and update.effective_user.id == ALLOWED_USER_ID
    )


# ── /start ────────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorised(update):
        return
    has_resume = load_resume_data() is not None
    status = (
        "✅ Resume data is loaded and ready."
        if has_resume
        else "⚠️ No resume set yet — use /setresume to paste yours."
    )
    await update.message.reply_text(
        "👋 *Resume Bot* is ready!\n\n"
        f"{status}\n\n"
        "Once your resume is set, simply paste a job description and I'll "
        "tailor your resume and return a compiled PDF.\n\n"
        "/setresume – Set or update your resume\n"
        "/showresume – View stored resume data\n"
        "/clearresume – Delete stored resume data\n"
        "/help – Detailed usage guide",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /help ─────────────────────────────────────────────────────────────────────


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorised(update):
        return
    await update.message.reply_text(
        "*How to use Resume Bot*\n\n"
        "*Step 1 — Set your resume (once)*\n"
        "Run /setresume, then paste your resume in the Markdown format "
        "shown in `resume_data.md.example`.  The bot saves it permanently.\n\n"
        "*Step 2 — Send any JD*\n"
        "Paste a job description.  The bot will:\n"
        "  1. Feed your full resume + the JD to Gemini AI\n"
        "  2. Get tailored Summary, Skills \\& Experience in LaTeX\n"
        "  3. Parse your static Heading \\& Education directly\n"
        "  4. Compile to PDF with pdflatex\n"
        "  5. Send you the finished PDF document\n\n"
        "*Markdown format for /setresume*\n"
        "```\n"
        "# Full Name\n\n"
        "## Contact\n"
        "- phone: …\n"
        "- email: …\n"
        "- website: …\n"
        "- linkedin: …\n\n"
        "## Summary\n"
        "Your summary paragraph.\n\n"
        "## Experience\n"
        "### Title | Dates | Company | Location\n"
        "- Bullet\n\n"
        "## Skills\n"
        "- Category: item1, item2\n\n"
        "## Education\n"
        "### Institution | Location | Degree | Dates\n"
        "```\n\n"
        "*Commands*\n"
        "/start · /help · /setresume · /showresume · /clearresume · /cancel",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /setresume conversation ───────────────────────────────────────────────────


async def cmd_setresume_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if not _is_authorised(update):
        return ConversationHandler.END
    existing = load_resume_data()
    verb = "update" if existing else "set"
    await update.message.reply_text(
        f"📄 Paste your resume in Markdown format to {verb} it.\n\n"
        "Use the structure from `resume_data.md.example` (see repo).\n\n"
        "Send /cancel to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return AWAITING_RESUME


async def cmd_setresume_receive(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if not _is_authorised(update):
        return ConversationHandler.END

    md_text = update.message.text.strip()

    if not md_text:
        await update.message.reply_text(
            "⚠️ Empty message.  Please paste your resume Markdown."
        )
        return AWAITING_RESUME

    # Basic sanity: must have at least a # Name heading
    if not re.search(r"^#\s+\S", md_text, re.MULTILINE):
        await update.message.reply_text(
            "⚠️ Doesn't look like valid resume Markdown — "
            "no `# Name` heading found.\n"
            "Please check the format and try again, or /cancel."
        )
        return AWAITING_RESUME

    save_resume_data(md_text)

    # Quick parse to surface any format issues early
    heading   = build_heading_latex(md_text)
    education = build_education_latex(md_text)

    issues: list[str] = []
    if not heading:
        issues.append("• Could not parse heading (check `# Name` and `## Contact`)")
    if not education:
        issues.append("• Could not parse education (check `## Education` section)")

    if issues:
        await update.message.reply_text(
            "✅ Resume saved — but with parsing warnings:\n"
            + "\n".join(issues)
            + "\n\nThe bot will still work; those sections may be blank. "
            "Use /showresume to review.",
        )
    else:
        await update.message.reply_text(
            "✅ Resume saved!  Heading and education parsed successfully.\n\n"
            "Now just send me a job description to get your tailored PDF. 🚀"
        )
    return ConversationHandler.END


async def cmd_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if not _is_authorised(update):
        return ConversationHandler.END
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ── /showresume ───────────────────────────────────────────────────────────────


async def cmd_showresume(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not _is_authorised(update):
        return
    data = load_resume_data()
    if not data:
        await update.message.reply_text(
            "No resume data set.  Use /setresume to add it."
        )
        return
    # Telegram message cap is 4096 chars
    snippet = data[:3800] + "\n… *(truncated)*" if len(data) > 3800 else data
    await update.message.reply_text(
        f"*Stored resume data:*\n\n```\n{snippet}\n```",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /clearresume ──────────────────────────────────────────────────────────────


async def cmd_clearresume(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not _is_authorised(update):
        return
    if RESUME_DATA_PATH.exists():
        RESUME_DATA_PATH.unlink()
        await update.message.reply_text("🗑️ Resume data cleared.")
    else:
        await update.message.reply_text(
            "Nothing to clear — no resume data is stored."
        )


# ── Main JD handler ───────────────────────────────────────────────────────────


async def handle_jd(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Main handler: receives JD → tailors → compiles → sends PDF."""
    if not _is_authorised(update):
        logger.warning(
            "Unauthorised access attempt from user ID %s",
            update.effective_user.id if update.effective_user else "unknown",
        )
        return

    resume_md = load_resume_data()
    if not resume_md:
        await update.message.reply_text(
            "⚠️ No resume data found.\n"
            "Use /setresume to paste your resume in Markdown format first."
        )
        return

    job_description = update.message.text.strip()
    if not job_description:
        await update.message.reply_text("Please send a non-empty job description.")
        return

    # ── Status ──────────────────────────────────────────────────────────────
    status_msg = await update.message.reply_text("⏳ Tailoring with AI…")

    # ── Step 1: LLM tailoring ───────────────────────────────────────────────
    try:
        summary, skills_tex, experience_tex = tailor_with_llm(
            resume_md, job_description
        )
    except Exception as exc:
        logger.exception("Unexpected error during LLM tailoring")
        await status_msg.edit_text(f"❌ AI tailoring failed: {exc}")
        return

    if not skills_tex or not experience_tex:
        await status_msg.edit_text(
            "❌ AI returned an incomplete response.  Please try again."
        )
        return

    # ── Step 2: Parse static sections ──────────────────────────────────────
    heading   = build_heading_latex(resume_md)
    education = build_education_latex(resume_md)

    # ── Step 3: Compile ─────────────────────────────────────────────────────
    await status_msg.edit_text("🔨 Compiling LaTeX PDF…")

    try:
        pdf_bytes, log_tail = compile_latex(
            heading, summary, skills_tex, experience_tex, education
        )
    except subprocess.TimeoutExpired:
        await status_msg.edit_text(
            "❌ Compilation timed out (>120 s).  Please try again."
        )
        return
    except Exception as exc:
        logger.exception("Unexpected error during LaTeX compilation")
        await status_msg.edit_text(f"❌ Compilation error: {exc}")
        return

    if pdf_bytes is None:
        diagnostic = log_tail or "No log available."
        snippet = diagnostic[-3800:] if len(diagnostic) > 3800 else diagnostic
        await status_msg.edit_text(
            f"❌ *pdflatex failed.*  Diagnostic log tail:\n\n```\n{snippet}\n```",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── Step 4: Send PDF ────────────────────────────────────────────────────
    await status_msg.edit_text("📤 Uploading…")

    company  = _infer_company(job_description)
    filename = f"{company}_Tailored_Resume.pdf" if company else "Tailored_Resume.pdf"

    await update.message.reply_document(
        document=io.BytesIO(pdf_bytes),
        filename=filename,
        caption=f"✅ Your tailored resume for *{company or 'the role'}*.",
        parse_mode=ParseMode.MARKDOWN,
    )
    await status_msg.delete()


# ── Utilities ─────────────────────────────────────────────────────────────────


def _infer_company(text: str) -> str:
    """Best-effort heuristic to extract a company name from the JD for the filename."""
    patterns = [
        r"(?:at|join|for|with)\s+([A-Z][A-Za-z0-9&\-]{1,30})",
        r"^([A-Z][A-Za-z0-9&\-]{1,30})\s+is\s+(?:looking|hiring|seeking)",
        r"([A-Z][A-Za-z0-9&\-]{1,30})\s+Job\s+Description",
    ]
    stopwords = {
        "The", "We", "Our", "You", "This", "A", "An", "Your",
        "About", "Role", "Position",
    }
    for pattern in patterns:
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            candidate = m.group(1).strip()
            if candidate not in stopwords:
                return re.sub(r"[^\w\-]", "_", candidate)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    logger.info("Starting Resume Bot (allowed user ID: %d)", ALLOWED_USER_ID)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # /setresume two-step conversation
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("setresume", cmd_setresume_start)],
        states={
            AWAITING_RESUME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cmd_setresume_receive
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("showresume",  cmd_showresume))
    app.add_handler(CommandHandler("clearresume", cmd_clearresume))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_jd))

    logger.info("Bot polling – press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
