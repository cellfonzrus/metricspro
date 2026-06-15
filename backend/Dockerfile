FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Headless Chromium for the epay Owner Portal sweep (#5b — WAF-protected SPA, Excel reports).
# Installs the browser + its OS libraries into the image. PLAYWRIGHT_BROWSERS_PATH=0 keeps the
# browser under the python package dir so it's found at runtime regardless of $HOME/user.
ENV PLAYWRIGHT_BROWSERS_PATH=0
RUN playwright install --with-deps chromium
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
