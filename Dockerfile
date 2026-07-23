# OpenFaceFX Studio — container image (self-host / SaaS).
#
#   docker build -t openfacefx-studio .
#   docker run --rm -p 8080:8080 -v offx-data:/data openfacefx-studio
#   → the studio is live at http://<host>:8080 with the native pipeline, real
#     accounts + per-user project storage + ciphertext-only vault sync
#     (studio_saas.py), and the stateless /api/llm relay (keys stay client-side).
#
# Accounts/projects live in the SQLite file at OFFX_STUDIO_DB (mount /data as a
# volume to persist them). Behind TLS, set OFFX_STUDIO_SECURE_COOKIE=1 so the
# session cookie is Secure. See docs/studio.md for the multi-tenant shape.
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# Persist accounts/projects/vault outside the container by default.
ENV OFFX_STUDIO_DB=/data/studio.db
VOLUME ["/data"]

EXPOSE 8080
# Bind all interfaces so the container is reachable; keep the browser closed.
CMD ["openfacefx", "studio", "--host", "0.0.0.0", "--port", "8080", "--no-open"]
