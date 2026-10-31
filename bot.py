#!/usr/bin/env python3
"""Single-user Telegram bot for producing source-grounded tailored resumes."""

from __future__ import annotations

import asyncio
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
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from resume_core import (
    ResumeFormatError,
    ResumeProfile,
    TailoringPlan,
    build_tailoring_coverage_report,
    build_tailoring_prompt,
    latex_escape,
    parse_resume_markdown,
    profile_to_markdown,
    render_education,
    render_experience,
    render_heading,
    render_skills,
    validate_tailoring_response,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv()

BASE_DIR = Path(__file__).parent
TEMPLATE_PATH = BASE_DIR / "resume_template.tex"
DATA_DIR = BASE_DIR / "data"
RESUME_DATA_PATH = DATA_DIR / "resume_data.md"
DEFAULT_MODEL_ID = "gemini-3.8-flash"
MODEL_ID = os.getenv("GEMINI_MODEL", DEFAULT_MODEL_ID)

(
    SETUP_NAME,
    SETUP_EMAIL,
    SETUP_PHONE,
    SETUP_WEBSITE,
    SETUP_LINKEDIN,
    SETUP_SUMMARY,
    SETUP_EXPERIENCE,
    SETUP_SKILLS,
    SETUP_EDUCATION,
    AWAITING_RESUME,
) = range(10)

SYSTEM_PROMPT = """\
You are a precise technical resume editor.
Return valid JSON only. Do not return Markdown or LaTeX.
Rewrite only claims supported by the candidate's cited source facts. Every
experience bullet must cite its source_fact_ids, and citations may only come from
the same role. The summary must cite summary_source_fact_ids. Never infer or
estimate metrics, scale, ownership, technologies, leadership, business outcomes,
or job-description keywords. Preserve source numbers exactly. Cover every
source-backed skill explicitly requested by the job description, then prefer
complete achievements over fragmented duties. Keep related action, implementation,
and impact facts in one bullet with all their citations; do not split a project
into standalone activity fragments to fill the bullet limit. Never invent impact.
Every source role must be represented by its role_index.
"""

TAILORING_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["summary", "summary_source_fact_ids", "experience", "skills"],
    "properties": {
        "summary": {"type": "string"},
        "summary_source_fact_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["role_index", "bullets"],
                "properties": {
                    "role_index": {"type": "integer"},
                    "bullets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["text", "source_fact_ids"],
                            "properties": {
                                "text": {"type": "string"},
                                "source_fact_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["category", "items"],
                "properties": {
                    "category": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

BOT_COMMANDS = (
    BotCommand("start", "Start setup or show bot status"),
    BotCommand("setup", "Create or replace your resume profile"),
    BotCommand("setresume", "Import a complete Markdown resume"),
    BotCommand("showresume", "Review your stored resume data"),
    BotCommand("exportdata", "Download your stored resume data"),
    BotCommand("help", "Show the command guide"),
    BotCommand("cancel", "Cancel the current profile entry"),
    BotCommand("deletedata", "Show how to delete stored resume data"),
)


def _required_environment() -> tuple[str, int, str]:
    names = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "GEMINI_API_KEY")
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    try:
        allowed_user_id = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_ALLOWED_USER_ID must be an integer.") from exc
    return os.environ["TELEGRAM_BOT_TOKEN"], allowed_user_id, os.environ["GEMINI_API_KEY"]


async def register_bot_commands(application: Application) -> None:
    """Publish the command menu used by Telegram's slash-command autocomplete."""
    await application.bot.set_my_commands(BOT_COMMANDS)


def load_resume_markdown() -> str | None:
    if not RESUME_DATA_PATH.exists():
        return None
    content = RESUME_DATA_PATH.read_text(encoding="utf-8").strip()
    return content or None


def load_profile() -> ResumeProfile | None:
    markdown = load_resume_markdown()
    return parse_resume_markdown(markdown) if markdown else None


def save_resume_markdown(markdown: str) -> ResumeProfile:
    profile = parse_resume_markdown(markdown)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESUME_DATA_PATH.write_text(profile_to_markdown(profile), encoding="utf-8")
    return profile


def tailor_with_llm(
    client: genai.Client, profile: ResumeProfile, job_description: str
) -> TailoringPlan:
    response = client.models.generate_content(
        model=MODEL_ID,
        contents=build_tailoring_prompt(profile, job_description),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=4096,
            response_mime_type="application/json",
            response_json_schema=TAILORING_RESPONSE_SCHEMA,
        ),
    )
    return validate_tailoring_response(response.text or "", profile, job_description)


def compile_latex(profile: ResumeProfile, plan: TailoringPlan) -> tuple[bytes | None, str]:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "%HEADING%": render_heading(profile),
        "%SUMMARY%": latex_escape(plan.summary),
        "%EXPERIENCE_SECTION%": render_experience(profile, plan),
        "%SKILLS_SECTION%": render_skills(plan),
        "%EDUCATION_SECTION%": render_education(profile),
    }
    for placeholder in replacements:
        if template.count(placeholder) != 1:
            return None, f"Template must contain {placeholder} exactly once."
    source = template
    for placeholder, content in replacements.items():
        source = source.replace(placeholder, content)

    command = [
        "pdflatex",
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        "resume.tex",
    ]
    with tempfile.TemporaryDirectory(prefix="resume_compile_") as directory:
        directory_path = Path(directory)
        (directory_path / "resume.tex").write_text(source, encoding="utf-8")
        for run_number in range(1, 3):
            result = subprocess.run(
                command,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            logger.info("pdflatex run %d exit code: %d", run_number, result.returncode)
            if result.returncode:
                log_path = directory_path / "resume.log"
                output = (
                    log_path.read_text(encoding="utf-8", errors="replace")
                    if log_path.exists()
                    else result.stderr or result.stdout
                )
                return None, output[-2000:]
        pdf_path = directory_path / "resume.pdf"
        return (pdf_path.read_bytes(), "") if pdf_path.exists() else (None, "PDF missing.")


def _allowed_user_id() -> int:
    try:
        return int(os.environ.get("TELEGRAM_ALLOWED_USER_ID", ""))
    except ValueError:
        return -1


def _is_authorised(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == _allowed_user_id())


async def _reject_unless_authorised(update: Update) -> bool:
    if _is_authorised(update):
        return False
    logger.warning(
        "Unauthorised access attempt from user ID %s",
        update.effective_user.id if update.effective_user else "unknown",
    )
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _reject_unless_authorised(update):
        return ConversationHandler.END
    try:
        profile = load_profile()
    except ResumeFormatError as exc:
        await update.message.reply_text(
            f"Your saved resume needs attention: {exc}\nUse /setresume to replace it."
        )
        return ConversationHandler.END
    if profile:
        await update.message.reply_text(
            f"Resume Bot is ready for {profile.name}.\n\n"
            "Paste a job description to generate a tailored PDF.\n"
            "Use /setup for guided replacement, /setresume for advanced Markdown input, "
            "/showresume to review data, or /help for all commands."
        )
        return ConversationHandler.END
    return await cmd_setup(update, context)


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _reject_unless_authorised(update):
        return ConversationHandler.END
    context.user_data["setup"] = {}
    await update.message.reply_text(
        "Let's build your resume profile step by step.\n\n"
        "Your profile is stored locally in data/resume_data.md. When you request "
        "a tailored resume, the profile and job description are sent to Google Gemini.\n\n"
        "First, what is your full name? Send /cancel at any time."
    )
    return SETUP_NAME


async def _record_setup_value(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    key: str,
    current_state: int,
    next_state: int,
    prompt: str,
    *,
    optional: bool = False,
) -> int:
    value = update.message.text.strip()
    if optional and value == "-":
        value = ""
    if not value and not optional:
        await update.message.reply_text("This value is required. Please try again.")
        return current_state
    context.user_data.setdefault("setup", {})[key] = value
    await update.message.reply_text(prompt)
    return next_state


async def setup_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_setup_value(
        update, context, "name", SETUP_NAME, SETUP_EMAIL, "What is your email address?"
    )


async def setup_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.message.text.strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        await update.message.reply_text("That email does not look valid. Please try again.")
        return SETUP_EMAIL
    return await _record_setup_value(
        update, context, "email", SETUP_EMAIL, SETUP_PHONE, "What is your phone number?"
    )


async def setup_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_setup_value(
        update,
        context,
        "phone",
        SETUP_PHONE,
        SETUP_WEBSITE,
        "Portfolio or personal website? Send - if you do not have one.",
    )


async def setup_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_setup_value(
        update,
        context,
        "website",
        SETUP_WEBSITE,
        SETUP_LINKEDIN,
        "LinkedIn profile? Send - if you do not want to include it.",
        optional=True,
    )


async def setup_linkedin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_setup_value(
        update,
        context,
        "linkedin",
        SETUP_LINKEDIN,
        SETUP_SUMMARY,
        "Send your current professional summary as one paragraph.",
        optional=True,
    )


async def setup_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_setup_value(
        update,
        context,
        "summary",
        SETUP_SUMMARY,
        SETUP_EXPERIENCE,
        "Send your experience in blocks like this:\n\n"
        "Job Title | Start - End | Company | Location\n"
        "- One atomic, verifiable achievement or responsibility\n"
        "- What you built, how you built it, and the verified result\n\n"
        "Repeat the block for each role. Include exact scale or metrics only when "
        "you can verify them; never estimate numbers.",
    )


async def setup_experience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_setup_value(
        update,
        context,
        "experience",
        SETUP_EXPERIENCE,
        SETUP_SKILLS,
        "Send skills one category per line:\n\n"
        "Frontend: React, TypeScript, CSS\n"
        "Tooling: Git, Docker",
    )


async def setup_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_setup_value(
        update,
        context,
        "skills",
        SETUP_SKILLS,
        SETUP_EDUCATION,
        "Send education, one entry per line:\n\n"
        "Institution | Location | Degree | Start - End",
    )


def _normalise_experience(value: str) -> str:
    lines: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if stripped and "|" in stripped and not stripped.startswith(("-", "*", "###")):
            lines.append(f"### {stripped}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _normalise_list(value: str, prefix: str) -> str:
    lines = []
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped if stripped.startswith(prefix) else f"{prefix}{stripped}")
    return "\n".join(lines)


def _profile_from_setup(data: dict[str, str]) -> ResumeProfile:
    contact_lines = [f"- phone: {data['phone']}", f"- email: {data['email']}"]
    if data.get("website"):
        contact_lines.append(f"- website: {data['website']}")
    if data.get("linkedin"):
        contact_lines.append(f"- linkedin: {data['linkedin']}")
    markdown = (
        f"# {data['name']}\n\n## Contact\n{'\n'.join(contact_lines)}\n\n"
        f"## Summary\n{data['summary']}\n\n"
        f"## Experience\n\n{_normalise_experience(data['experience'])}\n\n"
        f"## Skills\n{_normalise_list(data['skills'], '- ')}\n\n"
        f"## Education\n\n{_normalise_list(data['education'], '### ')}"
    )
    return parse_resume_markdown(markdown)


async def setup_education(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    setup_data = context.user_data.setdefault("setup", {})
    setup_data["education"] = update.message.text.strip()
    try:
        profile = _profile_from_setup(setup_data)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        RESUME_DATA_PATH.write_text(profile_to_markdown(profile), encoding="utf-8")
    except (KeyError, ResumeFormatError) as exc:
        await update.message.reply_text(
            f"I could not validate the profile: {exc}\n"
            "Please send the education entry again, or /cancel and restart /setup."
        )
        return SETUP_EDUCATION
    except OSError:
        logger.exception("Could not save the guided resume profile")
        await update.message.reply_text(
            "I could not save your profile because the bot's data directory is not "
            "writable. Your setup answers are still available in this running session. "
            "Fix the server-side data directory permissions, then send the education "
            "entry again."
        )
        return SETUP_EDUCATION
    context.user_data.pop("setup", None)
    await update.message.reply_text(
        "Your structured resume profile is saved. Paste a job description whenever "
        "you are ready to generate a tailored PDF."
    )
    return ConversationHandler.END


async def cmd_setresume_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _reject_unless_authorised(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "Advanced import: paste the complete resume Markdown using the format in "
        "resume_data.md.example. Send /cancel to abort."
    )
    return AWAITING_RESUME


async def cmd_setresume_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _reject_unless_authorised(update):
        return ConversationHandler.END
    try:
        profile = save_resume_markdown(update.message.text)
    except ResumeFormatError as exc:
        await update.message.reply_text(f"The resume is not valid yet: {exc}")
        return AWAITING_RESUME
    except OSError:
        logger.exception("Could not save the imported resume profile")
        await update.message.reply_text(
            "I could not save the resume because the bot's data directory is not "
            "writable. Fix the server-side data directory permissions, then send the "
            "resume again."
        )
        return AWAITING_RESUME
    await update.message.reply_text(
        f"Resume for {profile.name} saved and validated. Now send a job description."
    )
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _reject_unless_authorised(update):
        return ConversationHandler.END
    context.user_data.pop("setup", None)
    await update.message.reply_text("Cancelled. No profile changes were saved.")
    return ConversationHandler.END


async def cmd_showresume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unless_authorised(update):
        return
    markdown = load_resume_markdown()
    if not markdown:
        await update.message.reply_text("No resume is stored. Use /setup to create one.")
        return
    if len(markdown) <= 3900:
        await update.message.reply_text(f"Stored resume data:\n\n{markdown}")
    else:
        await update.message.reply_document(
            document=io.BytesIO(markdown.encode("utf-8")),
            filename="resume_data.md",
            caption="Stored resume data",
        )


async def cmd_exportdata(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unless_authorised(update):
        return
    markdown = load_resume_markdown()
    if not markdown:
        await update.message.reply_text("No resume is stored.")
        return
    await update.message.reply_document(
        document=io.BytesIO(markdown.encode("utf-8")),
        filename="resume_data.md",
        caption="Your locally stored resume data",
    )


async def cmd_clearresume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unless_authorised(update):
        return
    if context.args != ["confirm"]:
        await update.message.reply_text(
            "This permanently deletes the locally stored profile. "
            "Send /deletedata confirm to continue."
        )
        return
    if RESUME_DATA_PATH.exists():
        RESUME_DATA_PATH.unlink()
        await update.message.reply_text("Stored resume data deleted.")
    else:
        await update.message.reply_text("No resume data is stored.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unless_authorised(update):
        return
    await update.message.reply_text(
        "Resume Bot commands\n\n"
        "/setup - guided profile creation or replacement\n"
        "/setresume - advanced Markdown import\n"
        "/showresume - review stored data\n"
        "/exportdata - download stored data\n"
        "/deletedata confirm - permanently delete stored data\n"
        "/cancel - stop profile entry\n\n"
        "After setup, paste a job description directly into the chat. The bot sends "
        "your profile and that description to Gemini, validates every rewritten "
        "bullet against cited source facts, "
        "renders LaTeX locally, and returns a PDF."
    )


async def handle_jd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_unless_authorised(update):
        return
    try:
        profile = load_profile()
    except ResumeFormatError as exc:
        await update.message.reply_text(f"Stored resume is invalid: {exc}")
        return
    if not profile:
        await update.message.reply_text("No resume is stored. Use /setup first.")
        return
    job_description = update.message.text.strip()
    if len(job_description) < 40:
        await update.message.reply_text(
            "Please send a fuller job description (at least 40 characters)."
        )
        return

    lock = context.application.bot_data.get("generation_lock")
    if lock is None:
        lock = context.application.bot_data["generation_lock"] = asyncio.Lock()
    status = await update.message.reply_text(
        "Rewriting and ranking content from your validated fact pool…"
    )
    async with lock:
        try:
            plan = await asyncio.to_thread(
                tailor_with_llm,
                context.application.bot_data["genai_client"],
                profile,
                job_description,
            )
            if plan.warnings:
                logger.warning("Tailoring warnings: %s", "; ".join(plan.warnings))
            await status.edit_text("Rendering and compiling the PDF…")
            pdf_bytes, diagnostic = await asyncio.to_thread(compile_latex, profile, plan)
        except subprocess.TimeoutExpired:
            await status.edit_text("PDF compilation timed out. Please try again.")
            return
        except Exception:
            logger.exception("Resume generation failed")
            await status.edit_text("Resume generation failed. Check the bot logs and try again.")
            return

    if pdf_bytes is None:
        logger.error("pdflatex failed: %s", diagnostic)
        await status.edit_text("The PDF could not be compiled. Check the bot logs for details.")
        return
    filename = _build_resume_filename(profile, job_description)
    caption = "Tailored resume generated from provenance-checked source facts."
    if plan.warnings:
        caption += " Original source content was retained where needed to protect accuracy or relevant detail."
    await update.message.reply_document(
        document=io.BytesIO(pdf_bytes), filename=filename, caption=caption
    )
    await update.message.reply_text(
        build_tailoring_coverage_report(profile, plan, job_description)
    )
    await status.delete()


_GENERIC_JOB_METADATA = {
    "about us",
    "company",
    "company name",
    "our company",
    "our team",
    "the company",
    "the role",
}


def _metadata_value(value: str) -> str:
    cleaned = value.split("|", 1)[0].strip(" \t:-–—")
    if not cleaned or cleaned.casefold() in _GENERIC_JOB_METADATA:
        return ""
    return cleaned if len(cleaned) <= 80 else ""


def _labeled_job_value(text: str, labels: str) -> str:
    match = re.search(
        rf"^\s*(?:{labels})\s*:\s*([^\n]+?)\s*$",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    return _metadata_value(match.group(1)) if match else ""


def _infer_job_metadata(text: str) -> tuple[str, str]:
    """Return a company and role only when the JD states them explicitly."""
    company = _labeled_job_value(
        text, r"company(?:\s+name)?|organization|organisation|employer"
    )
    role = _labeled_job_value(text, r"job\s+title|position|role")

    if not company or not role:
        at_match = re.search(
            r"^\s*([A-Z][A-Za-z0-9 /&+.#()'-]{2,80}?)\s+at\s+"
            r"([A-Z][A-Za-z0-9 &+.#()'-]{1,80}?)\s*$",
            text,
            re.MULTILINE,
        )
        if at_match:
            role = role or _metadata_value(at_match.group(1))
            company = company or _metadata_value(at_match.group(2))

    if not company:
        about_match = re.search(
            r"^\s*About\s+([A-Z][A-Za-z0-9 &+.#()'-]{1,80}?)\s*$",
            text,
            re.MULTILINE,
        )
        if about_match:
            company = _metadata_value(about_match.group(1))

    if not company:
        hiring_match = re.search(
            r"^\s*([A-Z][A-Za-z0-9 &+.#()'-]{1,80}?)\s+is\s+"
            r"(?:hiring|looking|seeking)\b",
            text,
            re.MULTILINE,
        )
        if hiring_match:
            company = _metadata_value(hiring_match.group(1))

    return company, role


def _filename_part(value: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^\w]+", "_", value, flags=re.UNICODE).strip("_")
    return cleaned[:limit].rstrip("_")


def _build_resume_filename(profile: ResumeProfile, job_description: str) -> str:
    company, role = _infer_job_metadata(job_description)
    parts = [_filename_part(profile.name)]
    parts.extend(part for part in (_filename_part(company), _filename_part(role)) if part)
    if len(parts) > 1:
        parts.append("Resume")
    return "_".join(parts) + ".pdf"


def main() -> None:
    token, allowed_user_id, api_key = _required_environment()
    logger.info("Starting Resume Bot for configured user ID %d", allowed_user_id)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    application = (
        Application.builder()
        .token(token)
        .post_init(register_bot_commands)
        .build()
    )
    application.bot_data["genai_client"] = genai.Client(api_key=api_key)
    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("setup", cmd_setup),
            CommandHandler("setresume", cmd_setresume_start),
        ],
        states={
            SETUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_name)],
            SETUP_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_email)],
            SETUP_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_phone)],
            SETUP_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_website)],
            SETUP_LINKEDIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_linkedin)],
            SETUP_SUMMARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_summary)],
            SETUP_EXPERIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_experience)],
            SETUP_SKILLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_skills)],
            SETUP_EDUCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_education)],
            AWAITING_RESUME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_setresume_receive)
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conversation)
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("showresume", cmd_showresume))
    application.add_handler(CommandHandler("exportdata", cmd_exportdata))
    application.add_handler(CommandHandler("clearresume", cmd_clearresume))
    application.add_handler(CommandHandler("deletedata", cmd_clearresume))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_jd))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
