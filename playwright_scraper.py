import os
import asyncio
import traceback
from flask import Flask, jsonify, request
from flask_cors import CORS
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)

# --- FUNÇÕES DE UTILIDADE ---

def extract_data_from_html(html_content):
    """Extrai os dados da tabela de resultados a partir do HTML bruto."""
    if not html_content or html_content.strip() == "":
        return [], "HTML vazio"
    
    # Garante que o conteúdo seja interpretado como uma tabela completa
    if "<table" not in html_content.lower():
        html_content = f"<table>{html_content}</table>"
    
    soup = BeautifulSoup(html_content, 'html.parser')
    table = soup.find('table')

    if not table:
        return [], "Tabela não encontrada."

    # Captura os cabeçalhos (TH)
    headers = [header.text.strip() for header in table.find_all('th')]
    if not headers:
        return [], "Cabeçalhos não encontrados."

    data = []
    tbody = table.find('tbody')
    rows = tbody.find_all('tr') if tbody else table.find_all('tr')

    for row in rows:
        cells = row.find_all('td')
        # Verifica se é a linha de "Nenhum registro encontrado"
        if len(cells) == 1 and "nenhum registro" in cells[0].text.lower():
            continue
            
        if cells:
            row_data = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                row_data[key] = cell.text.strip()
            data.append(row_data)

    return data, None

# --- CORE DO SCRAPER ---

async def fetch_data_with_playwright(cpf=None, nome=None):
    """Realiza a busca no TCM-GO com suporte a paginação e múltiplos filtros."""
    url = "https://www.tcmgo.tc.br/site/portal-da-transparencia/consulta-de-contratos-de-pessoal/"
    log_messages = []
    all_records = []

    def log(msg):
        log_messages.append(msg)
        print(msg)

    async with async_playwright() as p:
        # Launch do browser (headless=True para produção)
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()

        try:
            log(f"Acessando portal TCM-GO...")
            await page.goto(url, wait_until="networkidle", timeout=60000)

            # Localizar Iframe da consulta
            iframe_handle = await page.wait_for_selector("iframe[src*='consulta-ato-pessoal']", timeout=20000)
            frame = await iframe_handle.content_frame()
            if not frame:
                return None, "Não foi possível acessar o conteúdo do iframe.", log_messages

            # Seletores PrimeFaces (escapando os dois pontos)
            selector_cpf = "#pesquisaAtos\\:cpf"
            selector_nome = "#pesquisaAtos\\:nome"
            selector_btn = "#pesquisaAtos\\:abrirAtos"

            await frame.wait_for_selector(selector_btn, state="visible")

            # Preenchimento conforme o parâmetro disponível
            if cpf:
                log(f"Filtrando por CPF: {cpf}")
                await frame.fill(selector_cpf, cpf.replace(".", "").replace("-", ""))
            elif nome:
                log(f"Filtrando por Nome: {nome}")
                await frame.fill(selector_nome, nome.upper())

            await frame.click(selector_btn)
            log("Busca enviada. Iniciando coleta de páginas...")

            # LOOP DE PAGINAÇÃO
            pagina = 1
            while True:
                log(f"Lendo dados da página {pagina}...")
                
                # Espera a tabela atualizar (tbody específico do PrimeFaces)
                tbody_selector = "tbody#form\\:mytable_data"
                try:
                    await frame.wait_for_selector(tbody_selector, timeout=15000)
                except:
                    log("Tempo de espera da tabela esgotado ou nenhum registro.")
                    break

                # Extrai HTML da tabela nesta página
                table_html = await frame.inner_html("table")
                page_records, err = extract_data_from_html(table_html)
                
                if page_records:
                    all_records.extend(page_records)
                    log(f"Página {pagina}: {len(page_records)} registros encontrados.")

                # Verifica se existe o botão "Próximo" e se está habilitado
                next_btn_selector = ".ui-paginator-next"
                next_btn = await frame.query_selector(next_btn_selector)
                
                if next_btn:
                    classes = await next_btn.get_attribute("class") or ""
                    if "ui-state-disabled" in classes:
                        log("Última página atingida.")
                        break
                    else:
                        await next_btn.click()
                        await page.wait_for_timeout(1500) # Delay para o AJAX
                        pagina += 1
                else:
                    break

            return all_records, None, log_messages

        except Exception as e:
            log(f"Erro no processo: {str(e)}")
            return None, str(e), log_messages
        finally:
            await browser.close()

# --- ENDPOINT API ---

@app.route('/api/buscar-registro', methods=['POST'])
def buscar_registro():
    data = request.get_json(force=True)
    cpf = data.get("cpf")
    nome = data.get("nome")

    if not cpf and not nome:
        return jsonify({"success": False, "message": "Informe CPF ou Nome"}), 400

    try:
        results, error, logs = asyncio.run(fetch_data_with_playwright(cpf=cpf, nome=nome))
        
        if error:
            return jsonify({"success": False, "error": error, "logs": logs}), 500
        
        return jsonify({
            "success": True,
            "count": len(results),
            "data": results,
            "logs": logs
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)