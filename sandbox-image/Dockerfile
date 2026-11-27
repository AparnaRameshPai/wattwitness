# The sandbox image: the "glass room" every piece of AI-written code runs in.
# It contains only Python and data libraries. No secrets, no tools, no network access at runtime.
FROM python:3.11-slim

RUN pip install --no-cache-dir \
        pandas==2.2.3 numpy==2.1.3 matplotlib==3.9.2 scipy==1.14.1 \
        statsmodels==0.14.4 seaborn==0.13.2

# Unprivileged user; matplotlib and HOME point to /tmp because the rest of the filesystem is read-only.
RUN useradd --uid 1000 --create-home runner
ENV HOME=/tmp \
    MPLCONFIGDIR=/tmp \
    MPLBACKEND=Agg \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER runner
WORKDIR /work
