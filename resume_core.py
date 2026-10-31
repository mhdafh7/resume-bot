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
class TailoredBullet:
    text: str
    source_fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExperienceSelection:
    role_index: int
    bullets: tuple[TailoredBullet, ...]

    @property
    def bullet_indices(self) -> tuple[int, ...]:
        """Return source indices for compatibility with older callers and exports."""
        indices: list[int] = []
        for bullet in self.bullets:
            for fact_id in bullet.source_fact_ids:
                match = re.fullmatch(rf"r{self.role_index}f(\d+)", fact_id)
                if match:
                    indices.append(int(match.group(1)))
        return tuple(dict.fromkeys(indices))


@dataclass(frozen=True)
class SkillSelection:
    category: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class TailoringPlan:
    summary: str
    experience: tuple[ExperienceSelection, ...]
    skills: tuple[SkillSelection, ...]
    summary_source_fact_ids: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


CURRENT_ROLE_BULLET_LIMIT = 6
EARLIER_ROLE_BULLET_LIMIT = 2
TOTAL_SKILL_LIMIT = 24
SKILLS_PER_CATEGORY_LIMIT = 8
MAX_BULLET_CHARACTERS = 600
MAX_SUMMARY_CHARACTERS = 520

_WORD_RE = re.compile(r"[a-z0-9+#.]+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?:%|\+|x)?", re.IGNORECASE)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "in", "is", "it", "of", "on", "or", "our", "that", "the", "their", "this",
    "to", "using", "we", "with", "you", "your",
}
_COMMON_TECHNOLOGY_NAMES = {
    "angular", "ansible", "aws", "azure", "cassandra", "cypress", "django", "docker",
    "dynamodb", "elasticsearch", "express", "fastapi", "figma", "firebase", "flask",
    "gatsby", "gcp", "git", "github", "gitlab", "go", "golang", "grafana", "graphql",
    "java", "javascript", "jenkins", "jest", "kafka", "kotlin", "kubernetes", "linux",
    "mongodb", "mysql", "next.js", "node.js", "nuxt", "oracle", "php", "playwright",
    "postgres", "postgresql", "python", "rails", "react", "redis", "redux", "ruby",
    "rust", "sentry", "snowflake", "sonarqube", "spring", "sql", "svelte", "swift",
    "tailwind", "terraform", "typescript", "vite", "vitest", "vue", "webpack",
}

# These concepts connect equivalent JD and resume language without allowing the
# model to invent a keyword. They improve deterministic fallback ranking and the
# user-facing coverage report; the cited source facts remain the authority.
_JD_CONCEPTS = {
    "state management": (
        "state management", "zustand", "redux", "state machine",
        "consistent real-time state",
    ),
    "responsive UI": (
        "responsive", "user-centric", "user centered", "cross-platform",
    ),
    "modularity and scalability": (
        "modular", "modularity", "scalable", "scalability", "maintainability",
        "maintainable", "reusable", "component library", "design system",
        "microfrontend",
    ),
    "feature ownership": (
        "ownership", "from conception to deployment", "from scratch", "greenfield",
        "led", "architected", "spearheaded", "end-to-end",
    ),
    "cross-functional collaboration": (
        "cross-functional", "collaborative", "collaborate", "communication",
        "product managers", "product designers", "backend engineers", "designers",
    ),
    "frontend performance": (
        "application performance", "web performance", "performance", "optimise",
        "optimising", "optimize", "optimizing", "optimization", "lazy loading",
        "build time", "startup performance",
    ),
}

_SKILL_JD_ALIASES = {
    "javascript (es6+)": ("javascript",),
    "html5": ("html",),
    "css3": ("css",),
    "sass/scss": ("sass", "scss"),
    "tailwind css": ("tailwind", "tailwind css"),
    "zustand": ("zustand", "state management"),
    "responsive design": ("responsive", "responsive design", "user-centric"),
    "web accessibility (wcag)": ("accessibility", "accessible", "wcag"),
    "web accessibility (wcag 2.1 aa)": ("accessibility", "accessible", "wcag"),
    "web performance": ("web performance", "application performance", "performance"),
    "component libraries": ("component library", "component libraries", "modular"),
    "design systems": ("design system", "design systems", "reusable"),
    "single sign-on integration": ("single sign-on", "sso"),
    "git version control": ("git",),
    "gitlab ci/cd pipelines": ("gitlab", "gitlab ci/cd"),
    "restful apis": ("rest api", "rest apis", "restful api", "restful apis"),
}


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
    selections = {item.role_index: item.bullets for item in plan.experience}
    fallback = {
        item.role_index: item.bullets
        for item in default_tailoring_plan(profile).experience
    }
    rendered: list[str] = []
    for role_index, role in enumerate(profile.experience):
        selected_bullets = selections.get(role_index) or fallback[role_index]
        bullets = "\n".join(
            f"        \\resumeItem{{{latex_escape(bullet.text)}}}"
            for bullet in selected_bullets
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


def _fact_id(role_index: int, bullet_index: int) -> str:
    return f"r{role_index}f{bullet_index}"


def _normalise_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _WORD_RE.findall(value)
        if len(token) > 1 and token.casefold() not in _STOPWORDS
    }


def _contains_phrase(value: str, phrase: str) -> bool:
    return bool(
        re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", value, re.IGNORECASE)
    )


def _matches_any_phrase(value: str, phrases: tuple[str, ...]) -> bool:
    return any(_contains_phrase(value, phrase) for phrase in phrases)


def _triggered_job_concepts(job_description: str) -> tuple[str, ...]:
    return tuple(
        name
        for name, phrases in _JD_CONCEPTS.items()
        if _matches_any_phrase(job_description, phrases)
    )


def _relevance_score(text: str, job_description: str) -> int:
    if not job_description:
        return 0
    text_tokens = _tokens(text)
    target_tokens = _tokens(job_description)
    overlap = text_tokens & target_tokens
    exact_bonus = sum(
        3
        for token in text_tokens
        if len(token) >= 4 and re.search(rf"(?<!\w){re.escape(token)}(?!\w)", job_description, re.I)
    )
    concept_bonus = sum(
        6
        for phrases in _JD_CONCEPTS.values()
        if _matches_any_phrase(job_description, phrases)
        and _matches_any_phrase(text, phrases)
    )
    return len(overlap) + exact_bonus + concept_bonus


def _impact_score(text: str) -> int:
    """Prefer quantified achievements and ownership when relevance is comparable."""
    score = min(6, len(_numbers(text)) * 3)
    if re.search(
        r"\b(?:architected|led|spearheaded|built|developed|integrated|reduced|"
        r"accelerated|improved|modernized|migrated|delivered)\b",
        text,
        re.IGNORECASE,
    ):
        score += 2
    return score


def _tailoring_score(text: str, job_description: str) -> int:
    # Relevance intentionally dominates impact. A quantified but unrelated fact
    # should not displace direct evidence for a target requirement.
    return _relevance_score(text, job_description) * 10 + _impact_score(text)


def _source_bullets_for_role(
    role_index: int, role: Experience, job_description: str
) -> tuple[TailoredBullet, ...]:
    limit = CURRENT_ROLE_BULLET_LIMIT if role_index == 0 else EARLIER_ROLE_BULLET_LIMIT
    ranked = sorted(
        enumerate(role.bullets),
        key=lambda item: (-_tailoring_score(item[1], job_description), item[0]),
    )[:limit]
    return tuple(
        TailoredBullet(text, (_fact_id(role_index, bullet_index),))
        for bullet_index, text in ranked
    )


def _fallback_skills(
    profile: ResumeProfile, job_description: str
) -> tuple[SkillSelection, ...]:
    ranked: list[tuple[int, int, int, str, str]] = []
    for category_index, group in enumerate(profile.skills):
        for item_index, item in enumerate(group.items):
            ranked.append(
                (
                    _relevance_score(item, job_description),
                    category_index,
                    item_index,
                    group.name,
                    item,
                )
            )
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    chosen = ranked[:TOTAL_SKILL_LIMIT]
    category_order = sorted(
        {category for _, category, _, _, _ in chosen},
        key=lambda category: (
            -max(score for score, index, _, _, _ in chosen if index == category),
            category,
        ),
    )
    selected: list[SkillSelection] = []
    for category_index in category_order:
        group = profile.skills[category_index]
        items = tuple(
            item
            for _, _, _, _, item in sorted(
                (entry for entry in chosen if entry[1] == category_index),
                key=lambda entry: (-entry[0], entry[2]),
            )[:SKILLS_PER_CATEGORY_LIMIT]
        )
        if items:
            selected.append(SkillSelection(group.name, items))
    return tuple(selected)


def _skill_aliases(item: str) -> tuple[str, ...]:
    folded = item.casefold().strip()
    aliases = {folded, *_SKILL_JD_ALIASES.get(folded, ())}
    return tuple(sorted(aliases, key=len, reverse=True))


def _skill_matches_job(item: str, job_description: str) -> bool:
    return _matches_any_phrase(job_description, _skill_aliases(item))


def _matching_source_skills(
    profile: ResumeProfile, job_description: str
) -> tuple[tuple[int, int, str, str], ...]:
    return tuple(
        (category_index, item_index, group.name, item)
        for category_index, group in enumerate(profile.skills)
        for item_index, item in enumerate(group.items)
        if _skill_matches_job(item, job_description)
    )


def _merge_supported_job_skills(
    profile: ResumeProfile,
    selected: tuple[SkillSelection, ...] | list[SkillSelection],
    job_description: str,
) -> tuple[SkillSelection, ...]:
    """Guarantee that source-backed skills explicitly requested by the JD survive."""
    source_positions = {
        item.casefold(): (category_index, item_index, group.name, item)
        for category_index, group in enumerate(profile.skills)
        for item_index, item in enumerate(group.items)
    }
    requested_order = {
        item.casefold(): position
        for position, item in enumerate(
            item for group in selected for item in group.items
        )
        if item.casefold() in source_positions
    }
    requested = set(requested_order)
    mandatory = {
        item.casefold()
        for _, _, _, item in _matching_source_skills(profile, job_description)
    }
    candidates = requested | mandatory
    ranked = sorted(
        (source_positions[key] for key in candidates),
        key=lambda entry: (
            0 if entry[3].casefold() in mandatory else 1,
            -_relevance_score(entry[3], job_description),
            requested_order.get(entry[3].casefold(), TOTAL_SKILL_LIMIT + 1),
            entry[0],
            entry[1],
        ),
    )

    chosen: list[tuple[int, int, str, str]] = []
    category_counts: dict[int, int] = {}
    for entry in ranked:
        category_index = entry[0]
        if (
            entry[3].casefold() not in mandatory
            and category_counts.get(category_index, 0) >= SKILLS_PER_CATEGORY_LIMIT
        ):
            continue
        chosen.append(entry)
        category_counts[category_index] = category_counts.get(category_index, 0) + 1
        if len(chosen) >= max(TOTAL_SKILL_LIMIT, len(mandatory)):
            break

    category_order = tuple(dict.fromkeys(entry[0] for entry in chosen))
    return tuple(
        SkillSelection(
            profile.skills[category_index].name,
            tuple(entry[3] for entry in chosen if entry[0] == category_index),
        )
        for category_index in category_order
    )


def default_tailoring_plan(
    profile: ResumeProfile, warning: str = "", job_description: str = ""
) -> TailoringPlan:
    """Build a concise, deterministic plan when model output cannot be trusted."""
    warnings = (warning,) if warning else ()
    return TailoringPlan(
        summary=profile.summary,
        experience=tuple(
            ExperienceSelection(
                index,
                _source_bullets_for_role(index, role, job_description),
            )
            for index, role in enumerate(profile.experience)
        ),
        skills=_merge_supported_job_skills(
            profile, _fallback_skills(profile, job_description), job_description
        ),
        summary_source_fact_ids=("s0",),
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


def _numbers(value: str) -> set[str]:
    return {number.casefold() for number in _NUMBER_RE.findall(value)}


def _known_skills(profile: ResumeProfile) -> tuple[str, ...]:
    return tuple(
        sorted(
            (item for group in profile.skills for item in group.items if len(item) >= 3),
            key=len,
            reverse=True,
        )
    )


def _target_technology_terms(job_description: str) -> tuple[str, ...]:
    """Find technology-like capitalized terms that a model may copy from the JD."""
    candidates = re.findall(r"(?<!\w)[A-Za-z][A-Za-z0-9.+#/-]{1,}(?!\w)", job_description)
    terms = set()
    for candidate in candidates:
        term = candidate.strip("./-")
        if not term:
            continue
        folded = term.casefold()
        has_technology_punctuation = bool(re.search(r"[.+#/]", term))
        is_short_acronym = term.isupper() and 2 <= len(term) <= 10
        if folded in _COMMON_TECHNOLOGY_NAMES or has_technology_punctuation or is_short_acronym:
            terms.add(term)
    return tuple(sorted(terms, key=len, reverse=True))


def _summary_fact_map(profile: ResumeProfile) -> dict[str, str]:
    facts = {"s0": profile.summary}
    facts.update(
        {
            _fact_id(role_index, bullet_index): bullet
            for role_index, role in enumerate(profile.experience)
            for bullet_index, bullet in enumerate(role.bullets)
        }
    )
    return facts


def _validate_claim(
    value: Any,
    fact_ids: Any,
    fact_map: dict[str, str],
    profile: ResumeProfile,
    job_description: str,
    character_limit: int,
) -> tuple[str, tuple[str, ...]] | None:
    """Validate claims against the caller's permitted citation scope."""
    if not isinstance(value, str) or not isinstance(fact_ids, list):
        return None
    text = _normalise_text(value)
    if not text or len(text) > character_limit:
        return None
    valid_ids = tuple(
        dict.fromkeys(
            fact_id for fact_id in fact_ids
            if isinstance(fact_id, str) and fact_id in fact_map
        )
    )
    if not valid_ids or len(valid_ids) != len(fact_ids):
        return None
    source = " ".join(fact_map[fact_id] for fact_id in valid_ids)
    if not _numbers(text).issubset(_numbers(source)):
        return None
    for term in (*_known_skills(profile), *_target_technology_terms(job_description)):
        if _contains_phrase(text, term) and not _contains_phrase(source, term):
            return None
    return text, valid_ids


def _validate_summary(
    value: Any,
    fact_ids: Any,
    profile: ResumeProfile,
    job_description: str,
) -> tuple[str, tuple[str, ...]] | None:
    return _validate_claim(
        value, fact_ids, _summary_fact_map(profile), profile,
        job_description, MAX_SUMMARY_CHARACTERS,
    )


def _validate_rewritten_bullet(
    value: Any,
    role_index: int,
    profile: ResumeProfile,
    job_description: str,
) -> TailoredBullet | None:
    if not isinstance(value, dict):
        return None
    fact_map = {
        _fact_id(role_index, index): source
        for index, source in enumerate(profile.experience[role_index].bullets)
    }
    validated = _validate_claim(
        value.get("text"), value.get("source_fact_ids"), fact_map,
        profile, job_description, MAX_BULLET_CHARACTERS,
    )
    return TailoredBullet(*validated) if validated else None


def _retains_source_detail(source: str, output: str) -> bool:
    """Conservative lexical check, not a semantic equivalence judgement.

    Ignore a source's project label and ordinary stopwords. When a paraphrase
    cannot be verified mechanically, prefer the user's original wording.
    """
    source = re.sub(r"^[^:.!?]{1,85}:\s+", "", source)
    return (
        _tokens(source).issubset(_tokens(output))
        and _numbers(source).issubset(_numbers(output))
    )


def _preserve_role_evidence(
    role_index: int,
    role: Experience,
    bullets: tuple[TailoredBullet, ...],
    job_description: str,
) -> tuple[TailoredBullet, ...]:
    if not job_description:
        return bullets
    facts = {
        _fact_id(role_index, index): text
        for index, text in enumerate(role.bullets)
    }
    candidates: list[TailoredBullet] = []
    restored: set[str] = set()
    for bullet in bullets:
        missing = [
            fact_id for fact_id in bullet.source_fact_ids
            if _relevance_score(facts[fact_id], job_description) > 0
            and not _retains_source_detail(facts[fact_id], bullet.text)
        ]
        if missing:
            # Restore all citations of a combined rewrite so replacing it cannot
            # silently discard its other source facts.
            for fact_id in bullet.source_fact_ids:
                if fact_id not in restored:
                    candidates.append(TailoredBullet(facts[fact_id], (fact_id,)))
                    restored.add(fact_id)
        else:
            candidates.append(bullet)
    cited = {fact_id for bullet in candidates for fact_id in bullet.source_fact_ids}
    candidates.extend(
        TailoredBullet(text, (fact_id,))
        for fact_id, text in facts.items()
        if fact_id not in cited and _relevance_score(text, job_description) > 0
    )
    # Rank by source evidence: compression must not change an achievement's rank.
    candidates.sort(key=lambda bullet: -_tailoring_score(
        " ".join(facts[fact_id] for fact_id in bullet.source_fact_ids),
        job_description,
    ))
    limit = CURRENT_ROLE_BULLET_LIMIT if role_index == 0 else EARLIER_ROLE_BULLET_LIMIT
    chosen: list[TailoredBullet] = []
    used: set[str] = set()
    for bullet in candidates:
        if used.intersection(bullet.source_fact_ids):
            continue
        chosen.append(bullet)
        used.update(bullet.source_fact_ids)
        if len(chosen) == limit:
            break
    return tuple(chosen)


def validate_tailoring_response(
    raw: str, profile: ResumeProfile, job_description: str = ""
) -> TailoringPlan:
    """Accept concise rewrites only when they cite compatible source facts."""
    try:
        data = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return default_tailoring_plan(
            profile, f"Invalid AI response: {exc}", job_description
        )

    warnings: list[str] = []
    validated_summary = _validate_summary(
        data.get("summary"),
        data.get("summary_source_fact_ids"),
        profile,
        job_description,
    )
    if validated_summary is None:
        warnings.append(
            "AI summary was empty, too long, missing valid citations, or introduced "
            "unsupported claims."
        )
        summary = profile.summary
        summary_source_fact_ids = ("s0",)
    else:
        summary, summary_source_fact_ids = validated_summary
        summary_focus = _tokens(profile.summary) & _tokens(job_description)
        if not summary_focus.issubset(_tokens(summary)):
            summary, summary_source_fact_ids = profile.summary, ("s0",)
            warnings.append("Source summary retained to preserve job-relevant focus.")

    fallback = default_tailoring_plan(profile, job_description=job_description)
    fallback_roles = {item.role_index: item.bullets for item in fallback.experience}
    requested_roles: dict[int, tuple[TailoredBullet, ...]] = {}
    role_data = data.get("experience")
    if isinstance(role_data, list):
        for selection in role_data:
            if not isinstance(selection, dict):
                continue
            role_index = selection.get("role_index")
            if not isinstance(role_index, int) or not 0 <= role_index < len(profile.experience):
                continue
            proposed_bullets = selection.get("bullets")
            if not isinstance(proposed_bullets, list):
                continue
            limit = (
                CURRENT_ROLE_BULLET_LIMIT
                if role_index == 0
                else EARLIER_ROLE_BULLET_LIMIT
            )
            valid_list: list[TailoredBullet] = []
            seen_text: set[str] = set()
            for value in proposed_bullets:
                bullet = _validate_rewritten_bullet(
                    value, role_index, profile, job_description
                )
                if bullet and bullet.text.casefold() not in seen_text:
                    valid_list.append(bullet)
                    seen_text.add(bullet.text.casefold())
            valid = tuple(
                sorted(
                    valid_list,
                    key=lambda bullet: -_tailoring_score(
                        bullet.text, job_description
                    ),
                )[:limit]
            )
            if valid:
                requested_roles[role_index] = valid

    experience = tuple(
        ExperienceSelection(
            index,
            _preserve_role_evidence(
                index, role, requested_roles.get(index, fallback_roles[index]),
                job_description,
            ),
        )
        for index, role in enumerate(profile.experience)
    )
    if len(requested_roles) != len(profile.experience):
        warnings.append(
            "One or more AI experience rewrites failed provenance checks; "
            "concise source facts were retained."
        )

    if any(
        role.role_index in requested_roles
        and role.bullets != requested_roles[role.role_index]
        for role in experience
    ):
        warnings.append("Source experience retained to preserve job-relevant detail.")

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
                selected_skills.append(
                    SkillSelection(source.name, items[:SKILLS_PER_CATEGORY_LIMIT])
                )
            if sum(len(group.items) for group in selected_skills) >= TOTAL_SKILL_LIMIT:
                break
    if not selected_skills:
        warnings.append("AI skill selection was invalid; relevant source skills were retained.")
        selected_skills = list(fallback.skills)

    bounded_skills = _merge_supported_job_skills(
        profile, selected_skills, job_description
    )

    return TailoringPlan(
        summary=summary,
        experience=experience,
        skills=bounded_skills,
        summary_source_fact_ids=summary_source_fact_ids,
        warnings=tuple(warnings),
    )


def build_tailoring_prompt(profile: ResumeProfile, job_description: str) -> str:
    """Build a provenance-addressable rewriting prompt."""
    roles = [
        {
            "role_index": role_index,
            "title": role.title,
            "dates": role.dates,
            "company": role.company,
            "location": role.location,
            "source_facts": [
                {"fact_id": _fact_id(role_index, bullet_index), "text": bullet}
                for bullet_index, bullet in enumerate(role.bullets)
            ],
        }
        for role_index, role in enumerate(profile.experience)
    ]
    skills = [{"category": group.name, "items": list(group.items)} for group in profile.skills]
    source = {
        "original_summary": {"fact_id": "s0", "text": profile.summary},
        "experience": roles,
        "skills": skills,
    }
    return (
        "SOURCE FACTS:\n"
        f"{json.dumps(source, ensure_ascii=False, indent=2)}\n\n"
        "TARGET JOB DESCRIPTION:\n"
        f"{job_description}\n\n"
        "Return one JSON object with keys summary, summary_source_fact_ids, "
        "experience, and skills. First identify the JD's required skills, preferred "
        "skills, responsibilities, and role themes internally. Do not return that "
        "analysis. Prioritize supported requirement coverage, concrete mechanisms, "
        "measurable impact, ownership and collaboration. Include every exact JD skill that exists in the "
        "source skills. Write a concise two-sentence summary using only supported "
        "source facts, and cite those facts in summary_source_fact_ids. Summary "
        "citations may use s0 and experience fact IDs. "
        "Experience must contain every role. Each role object has role_index and "
        "bullets. Each bullet has text and source_fact_ids. Rewrite source facts "
        "only when needed for clarity. Prefer the original wording when already relevant. "
        "Preserve implementation mechanisms, scope, outcomes, and role focus. "
        "A rewritten bullet may cite one or more facts from the same role. Never "
        "add an outcome, metric, technology, scope, leadership claim, or keyword "
        "that is not present in its cited facts. Preserve every number exactly. "
        f"Use at most {CURRENT_ROLE_BULLET_LIMIT} bullets for role 0 and at most "
        f"{EARLIER_ROLE_BULLET_LIMIT} bullets for each earlier role. Select and order "
        "bullets so they collectively cover as many supported requirements as "
        "possible, then prefer quantified impact and ownership over duties. Give "
        "highly relevant achievements more space; shorten lower-value content first. "
        f"Rewritten bullets may use up to {MAX_BULLET_CHARACTERS} characters. "
        "Skills is an ordered list of objects with an exact source category and "
        "an ordered list of exact source items; target 24 skills, but keep all "
        "source-backed JD matches even if this requires more. Do not "
        "copy unsupported keywords merely because they appear in the job description."
    )


def build_tailoring_coverage_report(
    profile: ResumeProfile, plan: TailoringPlan, job_description: str
) -> str:
    """Summarize what the generated resume covers and where source evidence is thin."""
    selected_skill_keys = {
        item.casefold() for group in plan.skills for item in group.items
    }
    matching_skills = _matching_source_skills(profile, job_description)
    matched_skills = [
        item for _, _, _, item in matching_skills if item.casefold() in selected_skill_keys
    ]
    omitted_skills = [
        item for _, _, _, item in matching_skills if item.casefold() not in selected_skill_keys
    ]

    selected_text = " ".join(
        bullet.text for role in plan.experience for bullet in role.bullets
    )
    source_experience = " ".join(
        bullet for role in profile.experience for bullet in role.bullets
    )
    source_text = profile_to_markdown(profile)
    demonstrated_themes: list[str] = []
    omitted_themes: list[str] = []
    evidence_gaps: list[str] = []
    for name in _triggered_job_concepts(job_description):
        phrases = _JD_CONCEPTS[name]
        if _matches_any_phrase(selected_text, phrases):
            demonstrated_themes.append(name)
        elif _matches_any_phrase(source_experience, phrases):
            omitted_themes.append(name)
        else:
            evidence_gaps.append(name)

    technology_terms = tuple(
        dict.fromkeys(
            term.casefold()
            for term in _target_technology_terms(job_description)
            if term.casefold() in _COMMON_TECHNOLOGY_NAMES | {"html", "css"}
        )
    )
    source_skill_aliases = {
        alias
        for group in profile.skills
        for item in group.items
        for alias in _skill_aliases(item)
    }
    for technology in technology_terms:
        if technology in source_skill_aliases or _contains_phrase(source_text, technology):
            continue
        evidence_gaps.append(technology)

    def display(values: list[str], empty: str) -> str:
        return ", ".join(dict.fromkeys(values)) if values else empty

    lines = [
        "Tailoring coverage",
        f"Matched JD skills (listed): {display(matched_skills, 'none explicitly identified')}",
        f"Experience evidence (phrase matches): {display(demonstrated_themes, 'none explicitly identified')}",
    ]
    omitted_facts = [
        _fact_id(role_index, bullet_index)
        for role_index, role in enumerate(profile.experience)
        for bullet_index, source in enumerate(role.bullets)
        if _relevance_score(source, job_description) > 0
        and not _retains_source_detail(source, " ".join(
            bullet.text for selection in plan.experience
            if selection.role_index == role_index
            for bullet in selection.bullets
            if _fact_id(role_index, bullet_index) in bullet.source_fact_ids
        ))
    ]
    supported_but_omitted = omitted_skills + omitted_themes + omitted_facts
    if supported_but_omitted:
        lines.append(
            f"Supported evidence omitted: {display(supported_but_omitted, 'none')}"
        )
    lines.append(f"Evidence gaps: {display(evidence_gaps, 'none detected')}")
    return "\n".join(lines)
