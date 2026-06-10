# Build context = REPO ROOT (compose sets dockerfile: backend/Dockerfile).
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/hearth ./hearth
RUN pip install --no-cache-dir .
COPY --from=ui /ui/dist /app/static
EXPOSE 8420
CMD ["python", "-m", "hearth.main"]
