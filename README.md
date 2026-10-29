# resume-bot

A private, single-user Telegram bot that builds a structured resume profile,
tailors it to a job description with Gemini, renders a fixed LaTeX template, and
returns an ATS-readable PDF.

The model does **not** write LaTeX. It returns a structured tailoring plan that
can only select skills and experience bullets already present in the candidate's
fact pool. Python validates that plan, escapes all text, and renders the final
document deterministically.

## How it works

```text
/setup or Markdown import
          |
          v
validated local resume profile  +  job description
          |                              |
          +------------+-----------------+
                       v
              Gemini JSON plan
                       |
                       v
        source-fact and claim validation
                       |
                       v
          deterministic LaTeX rendering
                       |
                       v
              pdflatex -> Telegram PDF
```

The bot is intentionally restricted to one configured Telegram user.

## Requirements

- Docker 20 or newer
- Docker Compose v2
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user ID
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey)

The host does not need Python or TeX Live.

## Installation

Create the local environment file:

```bash
cp .env.example .env
```

Fill in the three required values:

```dotenv
TELEGRAM_BOT_TOKEN=replace_me
TELEGRAM_ALLOWED_USER_ID=123456789
GEMINI_API_KEY=replace_me
```

Optionally override the model:

```dotenv
GEMINI_MODEL=gemini-2.5-flash
```

The container defaults to UID/GID `1000:1000`. If your host account uses
different IDs, set `RESUME_BOT_UID` and `RESUME_BOT_GID` in `.env` so the bot
can write to `./data` without running as root.

Build and start the bot:

```bash
docker compose build
docker compose up -d
docker compose logs -f resume-bot
```

The first build installs TeX Live and can take several minutes.

## First-time setup

Open the bot in Telegram and send `/start`. When no profile exists, the bot
automatically begins a guided conversation for:

1. Name and contact information
2. Professional summary
3. Employment history and source achievement bullets
4. Skills grouped by category
5. Education

The bot validates the completed profile before saving it. Use `/cancel` to stop
without saving. Use `/setup` later to replace the profile through the same guided
flow.

### Experience input

Send one or more blocks in this format:

```text
Full Stack Engineer | Oct 2023 - Present | Example Ltd | Kochi, India
- Reduced build time by 40% by restructuring the frontend pipeline.
- Built accessible React interfaces used by 20,000 customers.

Software Engineer Intern | Jan 2023 - Sep 2023 | Startup | Remote
- Implemented REST APIs with Node.js and PostgreSQL.
```

### Skills input

```text
Frontend: TypeScript, React, Next.js, CSS
Backend: Node.js, PostgreSQL, REST APIs
Tooling: Git, Docker, Playwright
```

### Education input

```text
Institution | Location | Degree | Start - End
```

## Advanced Markdown import

`/setresume` imports the human-editable format documented in
[`resume_data.md.example`](resume_data.md.example). The complete document is
validated and rewritten into a canonical format before it replaces stored data.

## Tailoring a resume

After setup, paste a job description directly into the chat. The bot:

1. Sends the profile fact pool and job description to Gemini.
2. Requests JSON containing a summary, exact source skill names, and source
   experience bullet indices.
3. Rejects unknown skills, invalid bullet indices, and unsupported numeric claims.
4. Preserves every employment role and falls back to source content when model
   output fails validation.
5. Escapes and renders all LaTeX locally with shell execution disabled.
6. Returns the compiled PDF.

Gemini can still produce an inaccurate non-numeric summary claim. Review every
generated resume before submitting it.

## Commands

| Command | Purpose |
| --- | --- |
| `/start` | Start guided setup or show ready status |
| `/setup` | Replace the profile through guided setup |
| `/setresume` | Import a complete Markdown profile |
| `/showresume` | Display or download stored profile data |
| `/exportdata` | Download the canonical Markdown profile |
| `/deletedata confirm` | Permanently delete local profile data |
| `/cancel` | Cancel profile entry without saving |
| `/help` | Show the in-bot command guide |

`/clearresume confirm` remains as a compatibility alias for deletion.

## Privacy and security

- Secrets live in `.env`, which is excluded from Git and the Docker build context.
- Resume data lives in `data/resume_data.md`, which is also excluded from Git.
- The configured Telegram user ID is checked for every command and message.
- The service and `pdflatex` run as an unprivileged host user by default.
- The full resume profile and each job description are sent to Google Gemini when
  generating a tailored resume.
- Model output is treated as untrusted data and never compiled as LaTeX.
- `pdflatex` runs with `-no-shell-escape` in an isolated temporary directory.
- `/deletedata` requires an explicit confirmation argument.

If a credential appears in logs, chat output, screenshots, or commit history,
revoke it at the provider and replace it in `.env`.

## Development

Run the dependency-free core test suite:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile bot.py resume_core.py
```

Build the complete runtime, including Telegram, Gemini, and TeX dependencies:

```bash
docker compose build
```

`requirements.txt` lists direct dependencies. `requirements.lock` is the exact
Python 3.12 environment used by Docker. Refresh the lock intentionally after
reviewing upstream changes; do not edit it as part of unrelated work.

## Project layout

```text
bot.py                    Telegram conversations, AI call, PDF compilation
resume_core.py            Domain parser, validation, deterministic renderer
resume_template.tex       Fixed LaTeX document and formatting macros
resume_data.md.example    Advanced import example
tests/test_resume_core.py Core regression tests
requirements.txt          Direct Python dependencies
requirements.lock         Verified transitive dependency versions
Dockerfile                Python 3.12 and TeX Live runtime
docker-compose.yml        Service, secrets, persistence, and logging
data/                     Local profile storage (contents ignored by Git)
```

## Operations

```bash
# Status
docker compose ps

# Logs
docker compose logs --tail=100 resume-bot

# Restart after configuration changes
docker compose up -d --force-recreate

# Rebuild after source or dependency changes
docker compose up -d --build

# Stop without deleting profile data
docker compose down
```

## Troubleshooting

**The bot does not respond:** confirm the service is running and that
`TELEGRAM_ALLOWED_USER_ID` matches the account sending messages.

**The profile is rejected:** compare the experience and education separators with
the examples above. Every experience needs at least one bullet.

**Gemini output fails validation:** the bot retains safe source facts and notes the
fallback in the PDF caption. Review container logs for the validation warning.

**PDF compilation fails:** inspect `docker compose logs resume-bot`. Detailed TeX
diagnostics stay in logs rather than being sent through Telegram.
