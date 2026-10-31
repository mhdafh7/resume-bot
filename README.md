# resume-bot

A private, single-user Telegram bot that builds a structured resume fact pool,
tailors it to a job description with Gemini, renders a fixed LaTeX template, and
returns an ATS-readable PDF.

The model does **not** write LaTeX. It returns a schema-constrained tailoring plan
whose rewritten bullets must cite source fact IDs. Python verifies the citations,
numbers, role-specific technologies, content limits, and exact skill names before
escaping and rendering the final document deterministically.

## How it works

```text
/setup or Markdown import
          |
          v
validated local resume profile  +  job description
          |                              |
          +------------+-----------------+
                       v
       Gemini provenance-aware JSON plan
                       |
                       v
      provenance, claim, and size validation
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
GEMINI_MODEL=gemini-3.8-flash
```

The container defaults to UID/GID `1000:1000`. If your host account uses
different IDs, set `RESUME_BOT_UID` and `RESUME_BOT_GID` in `.env` so the bot
can write to `./data` without running as root.

Make sure the bind-mounted data directory is owned by that same UID/GID before
starting the container. For the default IDs:

```bash
mkdir -p data
sudo chown -R 1000:1000 data
chmod u+rwx data
```

If you override the IDs, use those values in the `chown` command instead.

Build and start the bot:

```bash
docker compose build
docker compose up -d
docker compose logs -f resume-bot
```

The first build installs TeX Live and can take several minutes.

## Using the bot

### Typical workflow

1. Open the bot chat in Telegram and type `/` to open the command menu.
2. Send `/start`. If no profile exists, the bot begins guided setup; otherwise it
   reports that it is ready.
3. Complete setup, or use `/setresume` to import a complete Markdown profile.
4. Paste the full job description as a normal message. Do not prefix it with a
   command.
5. Wait while the bot tailors, validates, renders, and compiles the resume.
6. Download the returned PDF and review every claim before applying.
7. Paste another job description whenever you need another tailored version.

The bot accepts messages only from `TELEGRAM_ALLOWED_USER_ID`. Other Telegram
accounts receive no resume data or command output.

### First-time profile setup

Open the bot in Telegram and send `/start`. When no profile exists, the bot
automatically begins a guided conversation for:

1. Name and contact information
2. Professional summary
3. Employment history and source achievement bullets
4. Skills grouped by category
5. Education

The bot validates the completed profile before saving it. Use `/cancel` to stop
without saving. Use `/setup` later to replace the profile through the same guided
flow. A replacement is saved only after the final education answer validates.

#### Experience input

Send one or more blocks in this format:

```text
Full Stack Engineer | Oct 2023 - Present | Example Ltd | Kochi, India
- Reduced build time by 40% by restructuring the frontend pipeline.
- Built accessible React interfaces used by 20,000 customers.

Software Engineer Intern | Jan 2023 - Sep 2023 | Startup | Remote
- Implemented REST APIs with Node.js and PostgreSQL.
```

Use one atomic, verifiable fact per bullet. Include technologies, scale, and
results when known, but never estimate a metric. The bot may rewrite a fact for
clarity; it will reject new numbers and technologies that are absent from the
cited source facts.

#### Skills input

```text
Frontend: TypeScript, React, Next.js, CSS
Backend: Node.js, PostgreSQL, REST APIs
Tooling: Git, Docker, Playwright
```

#### Education input

```text
Institution | Location | Degree | Start - End
```

## Advanced Markdown import

`/setresume` imports the human-editable format documented in
[`resume_data.md.example`](resume_data.md.example). The complete document is
validated and rewritten into a canonical format before it replaces stored data.
After sending `/setresume`, paste the entire Markdown document in the next message.
Use `/cancel` if you decide not to replace the profile.

## Tailoring a resume

After setup, paste a job description directly into the chat. The bot:

1. Sends the profile fact pool and job description to Gemini.
2. Requests schema-constrained JSON containing a summary, exact source skill
   names, and rewritten bullets with source fact IDs.
3. Rejects unknown skills, cross-role citations, unsupported numeric claims,
   role-specific technologies absent from cited facts, and oversized content.
4. Preserves every employment role and enforces a six-bullet limit for the current
   role, two bullets for earlier roles, and a 24-skill total limit.
5. Uses job-description relevance to choose concise source facts when model output
   fails validation; it never falls back to dumping the complete profile.
6. Escapes and renders all LaTeX locally with shell execution disabled.
7. Returns the compiled PDF.

Provenance checks substantially reduce unsupported claims, but they cannot prove
that every non-numeric paraphrase is semantically perfect. Review every generated
resume before submitting it.

## Commands

The bot publishes this command list to Telegram at startup, so supported commands
appear in slash-command autocomplete after the service connects. If the menu does
not appear after an update, restart the service and reopen the Telegram chat.

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

### Reviewing and managing stored data

- `/showresume` displays the profile in chat when it is short, or sends it as a
  Markdown file when it is too long for one message.
- `/exportdata` always downloads the canonical Markdown profile. Edit that file
  locally and import the complete updated version with `/setresume`.
- `/setup` replaces the profile through the guided conversation.
- `/cancel` abandons an active `/setup` or `/setresume` conversation without
  replacing the saved profile.
- `/deletedata` only displays the confirmation instructions. Send
  `/deletedata confirm` to permanently remove `data/resume_data.md`.

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
tests/test_bot_commands.py Telegram command-menu regression test
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

**Setup stops after education with `PermissionError` for
`/app/data/resume_data.md`:** the host `./data` directory is not writable by the
container UID/GID. Apply the ownership commands from Installation using the
values configured by `RESUME_BOT_UID` and `RESUME_BOT_GID`, then restart the
container and send `/setup` again.

**The profile is rejected:** compare the experience and education separators with
the examples above. Every experience needs at least one bullet.

**Gemini output fails validation:** the bot retains a concise, relevance-ranked
subset of safe source facts and notes the fallback in the PDF caption. Review
container logs for the validation warning.

**PDF compilation fails:** inspect `docker compose logs resume-bot`. Detailed TeX
diagnostics stay in logs rather than being sent through Telegram.
