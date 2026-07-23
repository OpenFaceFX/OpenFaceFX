# OpenFaceFX Studio — container image (self-host / SaaS).
#
#   docker build -t openfacefx-studio .
#   docker run --rm -p 8080:8080 openfacefx-studio
#   → the studio is live at http://<host>:8080 with the native pipeline
#     and the stateless /api/llm relay (bring-your-own-key stays client-side).
#
# For a multi-tenant SaaS, put this behind your auth/proxy and add the vault-sync
# + project-storage layer described in docs/studio.md; the frontend is unchanged.
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8080
# Bind all interfaces so the container is reachable; keep the browser closed.
CMD ["openfacefx", "studio", "--host", "0.0.0.0", "--port", "8080", "--no-open"]
