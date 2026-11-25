FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib TZ=America/Sao_Paulo
WORKDIR /opt/dari
RUN apt-get update && apt-get install -y --no-install-recommends tzdata fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 dari && useradd --uid 10001 --gid 10001 --no-create-home dari
COPY --chown=10001:10001 . .
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "tools.serve"]
