# Fake-QQ isolation acceptance image; deliberately excludes all persona data,
# host credentials, repository config, assets and live runtime state.
# Build with repository root as context. Pin the resulting image ID for runs.
FROM python:3.13-slim
RUN pip install --no-cache-dir websocket-client==1.9.0 websockets==16.0
WORKDIR /engine
COPY src/ /engine/src/
COPY tools/onebot_qq_run.py tools/onebot_mock_server.py /engine/tools/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
CMD ["python", "/engine/tools/onebot_qq_run.py", "--dry-run"]
