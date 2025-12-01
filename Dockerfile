# Usar uma imagem base oficial do Python
FROM python:3.10-slim

# Instalar o Chromium, chromedriver e outras dependências
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
 && rm -rf /var/lib/apt/lists/*

# Definir o diretório de trabalho no contêiner
WORKDIR /app

# Copiar o arquivo de dependências primeiro para aproveitar o cache do Docker
COPY requirements.txt .

# Instalar as dependências do Python
# Adicionamos --no-cache-dir para manter a imagem um pouco menor
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o resto do código da aplicação para o diretório de trabalho
COPY . .

# Comando para iniciar a aplicação usando Gunicorn
# O Render define a variável de ambiente PORT, e o Gunicorn irá se vincular a ela.
# Usamos 0.0.0.0 para que o contêiner aceite conexões de fora.
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--timeout", "120", "selenium_scraper:app"]
