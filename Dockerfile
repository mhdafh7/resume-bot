# ──────────────────────────────────────────────────────────────────────────────
# Dockerfile – resume-bot
# Multi-stage: builder installs Python deps, final image is lean.
# ──────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Python dependency builder ───────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install pip build tools
RUN pip install --upgrade pip wheel

# Build the reviewed, reproducible dependency set. requirements.txt remains the
# short list of direct dependencies used when intentionally refreshing the lock.
COPY requirements.txt requirements.lock ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.lock


# ── Stage 2: Runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Avoid interactive prompts during apt
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── System packages ───────────────────────────────────────────────────────────
# texlive-latex-base           – minimal LaTeX kernel
# texlive-latex-recommended    – common packages (geometry, enumitem, …)
# texlive-fonts-recommended    – standard font sets
# lmodern                      – Latin Modern fonts (lm*.tfm / lm*.pfb)
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base \
        texlive-latex-recommended \
        texlive-fonts-recommended \
        lmodern \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Python runtime ─────────────────────────────────────────────────────────
# Re-use the pre-built wheels from the builder stage
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl \
    && rm -rf /wheels

# ── Application source ────────────────────────────────────────────────────────
WORKDIR /app
COPY bot.py .
COPY resume_core.py .
COPY resume_template.tex .

# ── Run the bot ───────────────────────────────────────────────────────────────
CMD ["python3", "bot.py"]
