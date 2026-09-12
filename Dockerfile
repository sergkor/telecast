FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY telecast ./telecast
COPY prompts ./prompts
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["telecast", "serve"]
