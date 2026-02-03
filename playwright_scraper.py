import os
import time
import json
import asyncio
import traceback
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- FUNÇÕES DE UTILIDADE ---

def extract_data_from_html(html_content):
    """Extrai os dados da tabela de resultados a partir do HTML."""
    if not html_content or html_content.strip() == "":
        return [], "HTML vazio"
    
    # Se o HTML não tem <table> mas tem <thead>/<tbody>, envolve com <table>
    if "<table" not in html_content.lower() and ("<thead" in html_content.lower() or "<tbody" in html_content.lower()):
        html_content = f"<table>{html_content}</table>"
    
    soup = BeautifulSoup(html_content, 'html.parser')
    table = soup.find('table')

    if not table:
        return [], "Tabela de resultados não encontrada no HTML."

    headers = [header.text.strip() for header in table.find_all('th')]
    if not headers:
        return [], "Nenhum header encontrado na tabela"

    data = []
    tbody = table.find('tbody')
    rows = tbody.find_all('tr') if tbody else table.find_all('tr')
    
    if not rows:
        return [], "Nenhuma linha encontrada na tabela"

    # verifica se é a linha vazia de "Nenhum registro encontrado"
    if len(rows) == 1:
        first_row_text = rows[0].text.strip()
        if "nenhum registro" in first_row_text.lower():
            return [], None  # Retorna lista vazia sem erro

    # processar linhas com dados
    for row in rows:
        cells = row.find_all('td')
        if not cells: continue 
        
        # Ignora linha de nenhum registro se ela aparecer no meio (raro)
        if len(cells) == 1 and "nenhum registro" in cells[0].text.lower():
            continue

        if len(cells) == len(headers):
            row_data = {headers[i]: cells[i].text.strip() for i in range(len(headers))}
            data.append(row_data)
        else:
            # fallback: tenta mapear mesmo com tamanhos diferentes
            row_data = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                row_data[key] = cell.text.strip()
            data.append(row_data)

    return data, None

def filter_records(records, municipio=None, cargo=None):
    """Filtra os registros baseado em município e cargo (match parcial/case-insensitive)."""
    if not records:
        return []
    
    filtered = []
    for r in records:
        match = True
        
        # Filtro de Município
        if municipio:
            mun_record = r.get('Município', '').lower()
            if municipio.lower() not in mun_record:
                match = False
        
        # Filtro de Cargo
        if match and cargo:
            cargo_record = r.get('Cargo / Tipo de Processo', '').lower()
            if cargo.lower() not in cargo_record:
                match = False
                
        if match:
            filtered.append(r)
            
    return filtered

# --- CORE DO SCRAPER ---

async def fetch_data_with_playwright(cpf=None, nome=None, municipio_filtro=None, cargo_filtro=None):
    """Busca dados usando Playwright com estratégias de fallback e suporte a paginação/filtros."""
    url = "https://www.tcmgo.tc.br/site/portal-da-transparencia/consulta-de-contratos-de-pessoal/"
    log_messages = []
    all_records = []

    def log(message):
        log_messages.append(message)
        print(message)

    async with async_playwright() as p:
        launch_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        browser = await p.chromium.launch(headless=True, args=launch_args)
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        context = await browser.new_context(user_agent=user_agent, viewport={"width":1920, "height":1080})
        page = await context.new_page()
        
        try:
            log(f"Acessando página. Alvo -> CPF: {cpf}, Nome: {nome}")

            # --- NAVEGAÇÃO ROBUSTA ---
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
                log("page.goto networkidle OK")
            except Exception as e_net:
                log(f"networkidle timeout. Tentando domcontentloaded...")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e_dom:
                    return None, f"Timeout total ao acessar a página: {e_dom}", log_messages

            # --- BUSCA DE IFRAME ROBUSTA ---
            log("Procurando iframe...")
            iframe_handle = await page.query_selector("iframe[src*='consulta-ato-pessoal']")
            if not iframe_handle:
                # Fallback: procura qualquer iframe que pareça ser o correto
                iframes = await page.query_selector_all("iframe")
                for i, ifr in enumerate(iframes):
                    src = await ifr.get_attribute("src")
                    if ifr and src and ("consulta" in src.lower() or "pessoal" in src.lower()):
                        iframe_handle = ifr
                        log(f"  -> Usando iframe {i} encontrado por fallback de SRC")
                        break
                if not iframe_handle and iframes:
                    iframe_handle = iframes[0] # Último recurso
            
            if not iframe_handle:
                return None, "Iframe de consulta não encontrado.", log_messages

            frame = await iframe_handle.content_frame()
            if not frame:
                return None, "Não foi possível acessar o frame do iframe.", log_messages

            log("Iframe acessado com sucesso.")
            
            # --- INTERAÇÃO COM FORMULÁRIO ---
            # Seletores PrimeFaces (escapando os dois pontos)
            selector_cpf = "#pesquisaAtos\\:cpf"
            selector_nome = "#pesquisaAtos\\:form_nome" # ID CORRIGO
            selector_btn = "#pesquisaAtos\\:abrirAtos"

            try:
                await frame.wait_for_selector(selector_btn, state="visible", timeout=15000)
            except:
                return None, "Botão de consulta não apareceu.", log_messages

            if cpf:
                log(f"Preenchendo CPF: {cpf}")
                cpf_limpo = cpf.replace(".", "").replace("-", "")
                await frame.fill(selector_cpf, cpf_limpo)
            elif nome:
                log(f"Preenchendo Nome: {nome}")
                await frame.fill(selector_nome, nome.upper())
                # TRUQUE DO TAB PARA PRIMEFACES RECONHECER O CAMPO
                await frame.press(selector_nome, "Tab")
                await page.wait_for_timeout(500)

            log("Clicando em Consultar...")
            await frame.click(selector_btn)
            
            # Espera robusta para AJAX
            log("Aguardando 10 segundos para processamento...")
            await page.wait_for_timeout(10000)

            # --- PAGINAÇÃO E COLETA ---
            pagina = 1
            while True:
                log(f"Lendo página {pagina}...")
                
                # Tenta esperar a tabela aparecer
                try:
                    await frame.wait_for_selector("tbody#form\\:mytable_data", timeout=10000)
                except:
                    log("Tabela não carregou ou timeout na paginação.")
                    break

                table_html = await frame.inner_html("table")
                page_records, err = extract_data_from_html(table_html)
                
                if page_records:
                    all_records.extend(page_records)
                    log(f"  + {len(page_records)} registros coletados na pág {pagina}.")
                else:
                    log("  Nenhum registro útil nesta página.")

                # Verifica botão "Próximo"
                next_btn = await frame.query_selector(".ui-paginator-next")
                if next_btn:
                    classes = await next_btn.get_attribute("class") or ""
                    if "ui-state-disabled" in classes:
                        log("Última página alcançada.")
                        break
                    else:
                        await next_btn.click()
                        await page.wait_for_timeout(2500) # Wait para próxima página carregar
                        pagina += 1
                else:
                    break

            # --- FILTRAGEM PÓS-BUSCA ---
            if municipio_filtro or cargo_filtro:
                count_before = len(all_records)
                log(f"Aplicando filtros: Mun='{municipio_filtro}', Cargo='{cargo_filtro}'")
                all_records = filter_records(all_records, municipio=municipio_filtro, cargo=cargo_filtro)
                log(f"Registros após filtro: {count_before} -> {len(all_records)}")

            return all_records, None, log_messages

        except Exception as e:
            msg_erro = f"Erro Playwright inesperado: {str(e)}"
            log(msg_erro)
            traceback.print_exc()
            return None, msg_erro, log_messages
        finally:
            try:
                await context.close()
            except: pass
            try:
                await browser.close()
            except: pass

# --- API FLASK ---

@app.route('/api/buscar-registro', methods=['POST'])
def buscar_registro():
    """Endpoint unificado para busca de registros (CPF ou Nome)."""
    data = request.get_json(force=True)
    cpf = data.get("cpf")
    nome = data.get("nome")
    municipio = data.get("municipio")
    cargo = data.get("cargo")

    if not cpf and not nome:
        return jsonify({"success": False, "message": "Informe CPF ou Nome"}), 400

    try:
        results, error, logs = asyncio.run(fetch_data_with_playwright(
            cpf=cpf, 
            nome=nome,
            municipio_filtro=municipio,
            cargo_filtro=cargo
        ))
        
        if error is not None:
             # Se retornou erro explícito, mas não crashou
             return jsonify({
                 "success": False, 
                 "error": error, 
                 "logs": logs
             }), 500
        
        # SUCESSO
        return jsonify({
            "success": True,
            "count": len(results),
            "data": results,
            "logs": logs
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Erro interno: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000, host="0.0.0.0")