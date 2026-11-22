FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Headless Chromium for the epay Owner Portal sweep (#5b — WAF-protected SPA, Excel reports).
# Installs the browser + its OS libraries into the image. PLAYWRIGHT_BROWSERS_PATH=0 keeps the
# browser under the python package dir so it's found at runtime regardless of $HOME/user.
ENV PLAYWRIGHT_BROWSERS_PATH=0
RUN playwright install --with-deps chromium
# Real Google Chrome (channel='chrome') for the Cloudflare-fronted VidaPay/T-CETRA login — genuine
# Chrome clears the managed challenge far more often than bundled Chromium. `|| true` so a Chrome
# install hiccup NEVER fails the image build: vidapay_sweep._launch() falls back to Chromium.
RUN playwright install --with-deps chrome || true
COPY . .
EXPOSE 8000
# Gunicorn with uvicorn workers so the API isn't a single event-loop process under punch-time load.
# Worker count from WEB_CONCURRENCY (default a safe 2). --timeout 120 matches slow selfie/DB round trips.
# Shell form so ${WEB_CONCURRENCY:-2} is expanded at container start.
# --forwarded-allow-ips: BELIEVE THE EDGE ABOUT THE CLIENT'S SCHEME (access probe run 326,
# index §CORE-43). Without it uvicorn's default of 127.0.0.1 discards the edge's
# `x-forwarded-proto: https`, scope["scheme"] stays "http", and every absolute URL the framework
# builds downgrades the caller to plaintext -- measured on production as
# `GET /api/v1/core/me/ -> 307 Location: http://...`, i.e. "resend your Bearer token in the clear".
# The container is reachable only through the edge, and no authorization decision reads this scheme.
# Override per-environment with FORWARDED_ALLOW_IPS. Rule + locks: app/core/forwarded_tls.py.
CMD gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:8000 --timeout 120 --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-*}
