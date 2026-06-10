# Multi-stage: build the SPA, then the Python image serves API + static UI.
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY backend/hearth ./hearth
COPY --from=ui /ui/dist ./static
EXPOSE 8420
CMD ["python", "-m", "hearth.main"]
