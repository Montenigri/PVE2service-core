FROM python:3.14-slim-bookworm AS builder
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1
COPY requirements.txt /app/
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PATH=/usr/local/bin:/install/bin:$PATH \
	PYTHONPATH=/app
COPY --from=builder /install /usr/local
# pve2proxy deploys via the ssh/scp CLI; the slim base image does not ship them.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends openssh-client \
	&& rm -rf /var/lib/apt/lists/*
# The import package lives in PVE2Services/ inside the repo: copying the repo
# root to /app yields /app/PVE2Services/... which PYTHONPATH=/app resolves.
COPY . /app
RUN useradd --create-home --home-dir /home/pve2 --shell /usr/sbin/nologin pve2 \
	&& chown -R pve2:pve2 /app /home/pve2
USER pve2
EXPOSE 8000
CMD ["uvicorn", "PVE2Services.core.wiki_service.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
