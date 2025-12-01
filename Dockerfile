FROM python:3.10-slim

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
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
