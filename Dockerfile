# Usando a imagem oficial do Playwright que já tem Python e todas as libs de sistema
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

# Copia apenas o requirements primeiro para aproveitar o cache do Docker
COPY requirements.txt .

# Instala as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante dos arquivos
COPY . .

# Instala apenas o navegador Chromium (sem precisar de permissões de root extras)
RUN playwright install chromium

# Porta padrão que o Render espera
EXPOSE 10000

# O PONTO CHAVE: Mudamos 'selenium_scraper' para 'playwright_scraper'
# Aumentamos o timeout para 120s pois scrapers podem demorar
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--timeout", "120", "playwright_scraper:app"]