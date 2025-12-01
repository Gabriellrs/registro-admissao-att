FROM python:3.10-slim

# Instalar dependências do sistema necessárias para Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgobject-2.0-0 \
    libnss3 \
    libnssutil3 \
    libsmime3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libgio-2.0-0 \
    libdrm2 \
    libexpat1 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    ca-certificates \
    curl \
    wget \
 && rm -rf /var/lib/apt/lists/*

# Definir o diretório de trabalho no contêiner
WORKDIR /app

# Copiar o arquivo de dependências
COPY requirements.txt .

# Instalar as dependências do Python e baixar navegadores Playwright
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium

# Copiar o resto do código da aplicação
COPY . .

# Comando para iniciar a aplicação usando Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--timeout", "120", "selenium_scraper:app"]
