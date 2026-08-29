FROM python:3.11-slim
WORKDIR /app
COPY app/ /app/app/
EXPOSE 8765
WORKDIR /app/app
CMD ["python3", "server.py"]