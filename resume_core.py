"""Resume parsing, tailoring-plan validation, and deterministic LaTeX rendering."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


class ResumeFormatError(ValueError):
    """Raised when resume Markdown cannot be converted into a profile."""


@dataclass(frozen=True)
class Contact:
    phone: str = ""
    email: str = ""
    website: str = ""
    linkedin: str = ""


@dataclass(frozen=True)
class Experience:
    title: str
    dates: str
    company: str
    location: str
    bullets: tuple[str, ...]


@dataclass(frozen=True)
class SkillCategory:
    name: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class Education:
    institution: str
    location: str
    degree: str
    dates: str


@dataclass(frozen=True)
class ResumeProfile:
    name: str
    contact: Contact
    summary: str
    experience: tuple[Experience, ...]
    skills: tuple[SkillCategory, ...]
    education: tuple[Education, ...]


@dataclass(frozen=True)
class ExperienceSelection:
    role_index: int
    bullet_indices: tuple[int, ...]


@dataclass(frozen=True)
class SkillSelection:
    category: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class TailoringPlan:
    summary: str
    experience: tuple[ExperienceSelection, ...]
    skills: tuple[SkillSelection, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _section_body(markdown: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=\n^##\s|\Z)"
    match = re.search(pattern, markdown, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _entry_blocks(body: str) -> list[tuple[str, str]]:
    headings = list(re.finditer(r"^###\s+(.+?)\s*$", body, re.MULTILINE))
    return [
        (
            match.group(1),
            body[match.end() : headings[index + 1].start() if index + 1 < len(headings) else len(body)],
        )
        for index, match in enumerate(headings)
    ]


def parse_resume_markdown(markdown: str) -> ResumeProfile:
    """Parse and validate the documented resume Markdown format."""
    name_match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    if not name_match:
        raise ResumeFormatError("Missing '# Full Name' heading.")

    contact_values: dict[str, str] = {}
    for line in _section_body(markdown, "Contact").splitlines():
        match = re.match(r"^[-*]\s+([\w -]+):\s*(.+?)\s*$", line)
        if match:
            contact_values[match.group(1).strip().lower()] = match.group(2).strip()

    summary = _section_body(markdown, "Summary")
    if not summary:
        raise ResumeFormatError("Missing or empty '## Summary' section.")

    experiences: list[Experience] = []
    for heading, block in _entry_blocks(_section_body(markdown, "Experience")):
        parts = [part.strip() for part in heading.split("|")]
        if len(parts) != 4:
            raise ResumeFormatError(
                "Each experience heading needs: Title | Dates | Company | Location."
            )
        bullets = tuple(
            match.group(1).strip()
            for match in re.finditer(r"^[-*]\s+(.+?)\s*$", block, re.MULTILINE)
        )
        if not bullets:
            raise ResumeFormatError(f"Experience '{parts[0]}' needs at least one bullet.")
        experiences.append(Experience(parts[0], parts[1], parts[2], parts[3], bullets))
    if not experiences:
        raise ResumeFormatError("Missing or empty '## Experience' section.")

    skills: list[SkillCategory] = []
    for line in _section_body(markdown, "Skills").splitlines():
        match = re.match(r"^[-*]\s+([^:]+):\s*(.+?)\s*$", line)
        if not match:
            continue
        items = tuple(item.strip() for item in match.group(2).split(",") if item.strip())
        if items:
            skills.append(SkillCategory(match.group(1).strip(), items))
    if not skills:
        raise ResumeFormatError("Missing or empty '## Skills' section.")

    education: list[Education] = []
    for heading, _ in _entry_blocks(_section_body(markdown, "Education")):
        parts = [part.strip() for part in heading.split("|")]
        if len(parts) != 4:
            raise ResumeFormatError(
                "Each education heading needs: Institution | Location | Degree | Dates."
            )
        education.append(Education(parts[0], parts[1], parts[2], parts[3]))
    if not education:
        raise ResumeFormatError("Missing or empty '## Education' section.")

    return ResumeProfile(
        name=name_match.group(1).strip(),
        contact=Contact(
            phone=contact_values.get("phone", ""),
            email=contact_values.get("email", ""),
            website=contact_values.get("website", ""),
            linkedin=contact_values.get("linkedin", ""),
        ),
        summary=" ".join(summary.split()),
        experience=tuple(experiences),
        skills=tuple(skills),
        education=tuple(education),
    )


def profile_to_markdown(profile: ResumeProfile) -> str:
    """Serialize a profile into the canonical, human-editable Markdown format."""
    lines = [f"# {profile.name}", "", "## Contact"]
    for key, value in (
        ("phone", profile.contact.phone),
        ("email", profile.contact.email),
        ("website", profile.contact.website),
        ("linkedin", profile.contact.linkedin),
    ):
        if value:
            lines.append(f"- {key}: {value}")

    lines.extend(["", "## Summary", profile.summary, "", "## Experience", ""])
    for role in profile.experience:
        lines.append(
            f"### {role.title} | {role.dates} | {role.company} | {role.location}"
        )
        lines.extend(f"- {bullet}" for bullet in role.bullets)
        lines.append("")

    lines.extend(["## Skills"])
    lines.extend(f"- {group.name}: {', '.join(group.items)}" for group in profile.skills)
    lines.extend(["", "## Education", ""])
    lines.extend(
        f"### {item.institution} | {item.location} | {item.degree} | {item.dates}"
        for item in profile.education
    )
    return "\n".join(lines).strip() + "\n"


_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(text: str) -> str:
    """Escape arbitrary plain text exactly once for insertion into LaTeX."""
    return "".join(_LATEX_ESCAPES.get(character, character) for character in text)


def _safe_link(value: str) -> str | None:
    if not value or re.search(r"[{}\\\x00-\x1f]", value):
        return None
    return value if re.match(r"^https?://", value, re.IGNORECASE) else f"https://{value}"


def render_heading(profile: ResumeProfile) -> str:
    parts: list[str] = []
    if profile.contact.phone:
        parts.append(latex_escape(profile.contact.phone))
    if profile.contact.email:
        email = profile.contact.email
        if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            parts.append(
                rf"\href{{mailto:{latex_escape(email)}}}"
                rf"{{\underline{{{latex_escape(email)}}}}}"
            )
        else:
            parts.append(latex_escape(email))
    for value in (profile.contact.website, profile.contact.linkedin):
        if not value:
            continue
        link = _safe_link(value)
        display = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
        if link:
            parts.append(
                rf"\href{{{latex_escape(link)}}}"
                rf"{{\underline{{{latex_escape(display)}}}}}"
            )
        else:
            parts.append(latex_escape(display))

    return (
        "\\begin{center}\n"
        rf"    \textbf{{\Huge \scshape {latex_escape(profile.name)}}} \\ \vspace{{1pt}}"
        "\n"
        rf"    \small {r' $|$ '.join(parts)}"
        "\n\\end{center}"
    )


def render_education(profile: ResumeProfile) -> str:
    return "\n\n".join(
        "    \\resumeSubheading\n"
        f"      {{{latex_escape(item.institution)}}}{{{latex_escape(item.location)}}}\n"
        f"      {{{latex_escape(item.degree)}}}{{{latex_escape(item.dates)}}}"
        for item in profile.education
    )


def render_experience(profile: ResumeProfile, plan: TailoringPlan) -> str:
    selections = {item.role_index: item.bullet_indices for item in plan.experience}
    rendered: list[str] = []
    for role_index, role in enumerate(profile.experience):
        indices = selections.get(role_index, tuple(range(len(role.bullets))))
        bullets = "\n".join(
            f"        \\resumeItem{{{latex_escape(role.bullets[index])}}}"
            for index in indices
        )
        rendered.append(
            "    \\resumeSubheading\n"
            f"      {{{latex_escape(role.title)}}}{{{latex_escape(role.dates)}}}\n"
            f"      {{{latex_escape(role.company)}}}{{{latex_escape(role.location)}}}\n"
            "      \\resumeItemListStart\n"
            f"{bullets}\n"
            "      \\resumeItemListEnd"
        )
    return "\n\n".join(rendered)


def render_skills(plan: TailoringPlan) -> str:
    return "\n".join(
        rf"    \small{{\item{{ \textbf{{{latex_escape(group.category)}}}"
        rf"{{: {latex_escape(', '.join(group.items))}}} \\ }}}}"
        for group in plan.skills
    )


def default_tailoring_plan(profile: ResumeProfile, warning: str = "") -> TailoringPlan:
    warnings = (warning,) if warning else ()
    return TailoringPlan(
        summary=profile.summary,
        experience=tuple(
            ExperienceSelection(index, tuple(range(len(role.bullets))))
            for index, role in enumerate(profile.experience)
        ),
        skills=tuple(SkillSelection(group.name, group.items) for group in profile.skills),
        warnings=warnings,
    )


def _extract_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("Tailoring response must be a JSON object.")
    return value


def validate_tailoring_response(raw: str, profile: ResumeProfile) -> TailoringPlan:
    """Accept only source-backed selections from a model JSON response."""
    try:
        data = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return default_tailoring_plan(profile, f"Invalid AI response: {exc}")

    warnings: list[str] = []
    proposed_summary = data.get("summary")
    summary = proposed_summary.strip() if isinstance(proposed_summary, str) else ""
    source_numbers = set(re.findall(r"\d+(?:\.\d+)?%?\+?", profile_to_markdown(profile)))
    summary_numbers = set(re.findall(r"\d+(?:\.\d+)?%?\+?", summary))
    if not summary or not summary_numbers.issubset(source_numbers):
        warnings.append("AI summary was empty or introduced unsupported numeric claims.")
        summary = profile.summary

    requested_roles: dict[int, tuple[int, ...]] = {}
    role_data = data.get("experience")
    if isinstance(role_data, list):
        for selection in role_data:
            if not isinstance(selection, dict):
                continue
            role_index = selection.get("role_index")
            bullet_indices = selection.get("bullet_indices")
            if not isinstance(role_index, int) or not 0 <= role_index < len(profile.experience):
                continue
            if not isinstance(bullet_indices, list):
                continue
            valid = tuple(
                dict.fromkeys(
                    index
                    for index in bullet_indices
                    if isinstance(index, int)
                    and 0 <= index < len(profile.experience[role_index].bullets)
                )
            )
            if valid:
                requested_roles[role_index] = valid

    experience = tuple(
        ExperienceSelection(
            index,
            requested_roles.get(index, tuple(range(len(role.bullets)))),
        )
        for index, role in enumerate(profile.experience)
    )

    source_categories = {group.name.casefold(): group for group in profile.skills}
    selected_skills: list[SkillSelection] = []
    skill_data = data.get("skills")
    if isinstance(skill_data, list):
        for selection in skill_data:
            if not isinstance(selection, dict) or not isinstance(selection.get("category"), str):
                continue
            source = source_categories.get(selection["category"].casefold())
            if not source or not isinstance(selection.get("items"), list):
                continue
            source_items = {item.casefold(): item for item in source.items}
            items = tuple(
                dict.fromkeys(
                    source_items[item.casefold()]
                    for item in selection["items"]
                    if isinstance(item, str) and item.casefold() in source_items
                )
            )
            if items:
                selected_skills.append(SkillSelection(source.name, items))
    if not selected_skills:
        warnings.append("AI skill selection was invalid; all source skills were retained.")
        selected_skills = [SkillSelection(group.name, group.items) for group in profile.skills]

    return TailoringPlan(summary, experience, tuple(selected_skills), tuple(warnings))


def build_tailoring_prompt(profile: ResumeProfile, job_description: str) -> str:
    """Build a compact, index-addressable fact-selection prompt."""
    roles = [
        {
            "role_index": role_index,
            "title": role.title,
            "dates": role.dates,
            "company": role.company,
            "location": role.location,
            "bullets": [
                {"bullet_index": bullet_index, "text": bullet}
                for bullet_index, bullet in enumerate(role.bullets)
            ],
        }
        for role_index, role in enumerate(profile.experience)
    ]
    skills = [{"category": group.name, "items": list(group.items)} for group in profile.skills]
    source = {
        "original_summary": profile.summary,
        "experience": roles,
        "skills": skills,
    }
    return (
        "SOURCE FACTS:\n"
        f"{json.dumps(source, ensure_ascii=False, indent=2)}\n\n"
        "TARGET JOB DESCRIPTION:\n"
        f"{job_description}\n\n"
        "Return one JSON object with keys: summary, experience, skills. "
        "summary is a concise 2-3 sentence rewrite using only source facts. "
        "experience is a list of objects with role_index and an ordered list of "
        "bullet_indices; include relevant bullets only, but include every role. "
        "skills is an ordered list of objects with an exact source category and "
        "an ordered list of exact source items. Never invent or rewrite a fact."
    )
