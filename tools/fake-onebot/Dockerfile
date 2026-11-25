FROM python:3.13-slim
RUN pip install --no-cache-dir 'websockets>=13,<17'
WORKDIR /opt/fake-onebot
COPY tools/onebot_mock_server.py ./server.py
RUN useradd --uid 10001 --create-home simulator && mkdir -p /evidence && chown simulator:simulator /evidence
USER simulator
ENV FAKE_ONEBOT_HOST=0.0.0.0 FAKE_ONEBOT_PORT=3001 FAKE_ONEBOT_HTTP_PORT=3002 FAKE_ONEBOT_EVIDENCE=/evidence/events.jsonl
CMD ["python", "server.py"]
