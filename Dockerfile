# ──────────────────────────────────────────────────────────────────────────────
# Dockerfile – resume-bot
# Multi-stage: builder installs Python deps, final image is lean.
# ──────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Python dependency builder ───────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install pip build tools
RUN pip install --upgrade pip wheel

# Copy and build wheels for all Python dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# ── Stage 2: Runtime image ────────────────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

# Avoid interactive prompts during apt
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── System packages ───────────────────────────────────────────────────────────
# texlive-latex-base           – minimal LaTeX kernel
# texlive-latex-recommended    – common packages (geometry, enumitem, …)
# texlive-fonts-recommended    – standard font sets
# lmodern                      – Latin Modern fonts (lm*.tfm / lm*.pfb)
# python3, python3-pip         – Python runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base \
        texlive-latex-recommended \
        texlive-fonts-recommended \
        lmodern \
        python3 \
        python3-pip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Python runtime ─────────────────────────────────────────────────────────
# Re-use the pre-built wheels from the builder stage
COPY --from=builder /wheels /wheels
RUN pip3 install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl \
    && rm -rf /wheels

# ── Application source ────────────────────────────────────────────────────────
WORKDIR /app
COPY bot.py .
COPY resume_template.tex .

# ── Run the bot ───────────────────────────────────────────────────────────────
CMD ["python3", "bot.py"]
