# selenium_scraper.py
import os
import time
import json
import asyncio
import traceback
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS

async def fetch_data_with_playwright(cpf_para_pesquisa):
    """Busca dados usando Playwright (navegadores sincronizados automaticamente)."""
    url = "https://www.tcmgo.tc.br/site/portal-da-transparencia/consulta-de-contratos-de-pessoal/"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print(f"Acessando a página para o CPF: {cpf_para_pesquisa}")
            await page.goto(url, wait_until="networkidle", timeout=30000)
            
            # Aguarda iframe
            print("Procurando iframe...")
            iframe_handle = await page.query_selector("iframe[src*='consulta-ato-pessoal']")
            if not iframe_handle:
                iframes = await page.query_selector_all("iframe")
                print(f"Total de iframes encontrados: {len(iframes)}")
                if iframes:
                    iframe_handle = iframes[0]
                else:
                    return None, "Iframe não encontrado"
            
            frame = await iframe_handle.content_frame()
            print("Dentro do iframe com sucesso")
            
            # Preenche CPF e clica no botão
            cpf_limpo = cpf_para_pesquisa.replace(".", "").replace("-", "")
            print(f"CPF inserido: {cpf_limpo}")
            await frame.fill("#pesquisaAtos\\:cpf", cpf_limpo)
            
            print("Clicando no botão de busca...")
            await frame.click("#pesquisaAtos\\:abrirAtos")
            
            # Aguarda resultados
            await page.wait_for_timeout(4000)
            print("Aguardando resultados...")
            
            # Tenta localizar a tabela com dados
            try:
                await frame.wait_for_selector("table tbody tr", timeout=10000)
                print("Tabela encontrada com dados")
            except:
                print("Aviso: tbody não encontrado, tentando mesmo assim...")
            
            # Pega HTML da tabela
            table_html = await frame.inner_html("table")
            if not table_html:
                print("Nenhuma tabela encontrada")
                return None, "Nenhum registro encontrado para o CPF informado."
            
            print("Tabela extraída com sucesso")
            return table_html, None
            
        except Exception as e:
            print(f"Erro Playwright: {e}")
            traceback.print_exc()
            return None, f"Erro ao buscar dados: {e}"
        finally:
            await browser.close()


def extract_data_from_html(html_content):
    """Extrai os dados da tabela de resultados a partir do HTML."""
    soup = BeautifulSoup(html_content, 'html.parser')
    table = soup.find('table')

    if not table:
        return [], "Tabela de resultados não encontrada no HTML processado."

    headers = [header.text.strip() for header in table.find_all('th')]
    print(f"Headers encontrados: {headers}")

    data = []
    tbody = table.find('tbody')
    if not tbody:
        return [], "tbody não encontrado"

    rows = tbody.find_all('tr')
    print(f"Linhas encontradas no tbody: {len(rows)}")
    
    if not rows:
        return [], "Nenhuma linha encontrada na tabela"

    # Verifica a primeira linha
    if len(rows) == 1:
        first_row_text = rows[0].text.strip()
        print(f"Texto da primeira (única) linha: '{first_row_text}'")
        if "nenhum registro" in first_row_text.lower() or first_row_text == "":
            return [], None

    for idx, row in enumerate(rows):
        cells = row.find_all('td')
        print(f"Linha {idx}: {len(cells)} células encontradas")
        
        for cell_idx, cell in enumerate(cells):
            cell_text = cell.text.strip()
            print(f"  Célula {cell_idx}: '{cell_text}'")
        
        row_data = {}
        if len(cells) == len(headers):
            for i, cell in enumerate(cells):
                row_data[headers[i]] = cell.text.strip()
            data.append(row_data)
        else:
            print(f"  Aviso: Número de células ({len(cells)}) não corresponde ao número de headers ({len(headers)})")
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                row_data[key] = cell.text.strip()
            data.append(row_data)

    print(f"Total de registros extraídos: {len(data)}")
    return data, None

# --- API Flask ---

app = Flask(__name__)
CORS(app)

@app.route('/api/buscar-registro-selenium', methods=['POST'])
def buscar_registro_selenium():
    """Endpoint para buscar registro de admissão por CPF."""
    payload = request.get_json(force=True)
    cpf = payload.get("cpf") if payload else None
    
    if not cpf:
        return jsonify({"message": "CPF não informado."}), 400

    try:
        html, err = asyncio.run(fetch_data_with_playwright(cpf))
        if err:
            return jsonify({"message": err}), 404
        
        records, err2 = extract_data_from_html(html)
        if err2:
            return jsonify({"message": err2}), 404
        
        print(f"Encontrados {len(records)} registros de admissão para o CPF.")
        return jsonify({"count": len(records), "records": records}), 200
        
    except Exception as e:
        print(f"Erro na requisição: {e}")
        traceback.print_exc()
        return jsonify({"message": f"Erro interno: {e}"}), 500
