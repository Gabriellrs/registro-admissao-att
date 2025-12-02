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
    """Busca dados usando Playwright com estratégias de fallback quando há timeout."""
    url = "https://www.tcmgo.tc.br/site/portal-da-transparencia/consulta-de-contratos-de-pessoal/"
    log_messages = []

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
            log(f"Acessando a página para o CPF: {cpf_para_pesquisa}")

            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
                log("page.goto networkidle OK")
            except Exception as e_net:
                log(f"networkidle timeout: {e_net}. Tentando domcontentloaded...")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    log("page.goto domcontentloaded OK")
                except Exception as e_dom:
                    log(f"domcontentloaded falhou: {e_dom}")
                    return None, f"Timeout ao acessar a página", log_messages

            log("Procurando iframe...")
            iframe_handle = await page.query_selector("iframe[src*='consulta-ato-pessoal']")
            if not iframe_handle:
                iframes = await page.query_selector_all("iframe")
                log(f"iframes encontrados (fallback): {len(iframes)}")
                for i, ifr in enumerate(iframes):
                    src = await ifr.get_attribute("src")
                    log(f"  iframe[{i}]: src='{src}'")
                    if ifr and ("consulta" in (src or "").lower() or "pessoal" in (src or "").lower()):
                        iframe_handle = ifr
                        log(f"  -> Usando iframe {i}")
                        break
                if not iframe_handle:
                    iframe_handle = iframes[0] if iframes else None
                if not iframe_handle:
                    return None, "Iframe não encontrado na página principal.", log_messages

            frame = await iframe_handle.content_frame()
            if not frame:
                log("ERRO: content_frame() retornou None")
                return None, "Não foi possível acessar o conteúdo do iframe.", log_messages

            log("Dentro do iframe com sucesso")
            
            # Espera explícita pelos seletores do CPF e do botão para garantir que estejam prontos
            try:
                await frame.wait_for_selector("#pesquisaAtos\\:cpf", state="visible", timeout=10000)
                await frame.wait_for_selector("#pesquisaAtos\\:abrirAtos", state="visible", timeout=10000)
                log("Seletores de CPF e botão estão visíveis no iframe.")
            except Exception as e_wait_selector:
                log(f"Erro ao aguardar seletores no iframe: {e_wait_selector}")
                return None, f"Elementos essenciais não encontrados no iframe: {e_wait_selector}", log_messages
            
            # debug: imprime seletores disponíveis no iframe
            input_exists = await frame.query_selector("#pesquisaAtos\\:cpf")
            btn_exists = await frame.query_selector("#pesquisaAtos\\:abrirAtos")
            table_exists = await frame.query_selector("table")
            log(f"Debug seletores no iframe: cpf_input={input_exists is not None}, button={btn_exists is not None}, table={table_exists is not None}")
            
            cpf_limpo = cpf_para_pesquisa.replace(".", "").replace("-", "")
            log(f"CPF limpo: {cpf_limpo}")

            # preencher e submeter
            try:
                await frame.fill("#pesquisaAtos\\:cpf", cpf_limpo, timeout=5000)
                log("CPF preenchido com sucesso")
                await frame.click("#pesquisaAtos\\:abrirAtos", timeout=5000)
                log("Botão clicado com sucesso")
            except Exception as e_fill:
                log(f"Erro ao preencher/clicar: {e_fill}")
                return None, f"Falha ao interagir com o formulário: {e_fill}", log_messages

            # aguardar mudanças no DOM
            log("Aguardando resultados (esperando mudança na tabela)...")
            try:
                await frame.wait_for_function(
                    """() => {
                        const tbody = document.querySelector('tbody#form\\\\:mytable_data');
                        if (!tbody) return false;
                        const rows = tbody.querySelectorAll('tr');
                        if (rows.length === 0) return false; // Aguarda se estiver vazio
                        if (rows.length > 1) return true; // Mais de 1 linha = dados carregados
                        // Se 1 linha, verifica se NÃO é a de "Nenhum registro"
                        const firstRowText = rows[0].textContent || rows[0].innerText;
                        return !firstRowText.includes('Nenhum registro');
                    }""",
                    timeout=15000
                )
                log("Tabela foi alterada (dados ou mensagem de vazio)")
            except Exception as e_wait:
                log(f"Timeout aguardando mudança na tabela: {e_wait}. Continuando...")



            # extrair HTML da tabela especificamente
            table_html = ""
            try:
                # tenta pegar apenas o HTML da tabela
                table_html = await frame.inner_html("table")
                log(f"Table HTML extraído (tamanho: {len(table_html)} chars)")
            except Exception as e_table:
                log(f"Erro ao extrair table com inner_html: {e_table}")
                # fallback: tenta get_attribute outerHTML
                try:
                    table_elem = await frame.query_selector("table")
                    if table_elem:
                        table_html = await table_elem.inner_html()
                        log(f"Table HTML extraído com fallback (tamanho: {len(table_html)} chars)")
                except Exception as e_fallback:
                    log(f"Fallback também falhou: {e_fallback}")

            if not table_html or table_html.strip() == "":
                log("ERRO: table_html vazio. Imprimindo frame.content() para debug...")
                try:
                    frame_content = await frame.content()
                    log("=== INÍCIO FRAME CONTENT COMPLETO ===")
                    log(frame_content[:5000])
                    log("=== FIM FRAME CONTENT ===")
                except Exception as e_content:
                    log(f"Erro ao obter frame.content(): {e_content}")
                return None, "Tabela de resultados não encontrada no HTML processado.", log_messages

            # verifica se contém a mensagem "Nenhum registro"
            if "Nenhum registro" in table_html:
                log("AVISO: Tabela contém 'Nenhum registro encontrado'")

            log("Tabela extraída com sucesso")
            return table_html, None, log_messages

        except Exception as e:
            log(f"Erro Playwright inesperado: {e}")
            traceback.print_exc() # Still print to console for server-side debugging
            return None, f"Erro ao buscar dados: {e}", log_messages
        finally:
            try:
                await context.close()
            except Exception:
                pass
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
        return [], "Nenhum registro encontrado para o CPF informado."

    # verifica se é a linha vazia de "Nenhum registro encontrado"
    if len(rows) == 1:
        first_row_text = rows[0].text.strip()
        print(f"Texto da primeira linha: '{first_row_text}'")
        if "nenhum registro" in first_row_text.lower():
            return [], None  # None = retornar mensagem padrão

    # processar linhas com dados
    for idx, row in enumerate(rows):
        cells = row.find_all('td')
        print(f"Linha {idx}: {len(cells)} células encontradas")
        
        if cells and len(cells) == len(headers):
            row_data = {headers[i]: cells[i].text.strip() for i in range(len(headers))}
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
    debug = payload.get("debug", False)  # novo parâmetro para debug
    
    if not cpf:
        return jsonify({"message": "CPF não informado."}), 400

    try:
        html, err, logs = asyncio.run(fetch_data_with_playwright(cpf))
        if err:
            return jsonify({"message": err, "logs": logs}), 404
        
        # se debug=true, retorna o HTML bruto para inspeção
        if debug:
            return jsonify({
                "debug": True,
                "html_raw": html,
                "message": "HTML bruto da tabela (debug mode)",
                "logs": logs
            }), 200
        
        records, err2 = extract_data_from_html(html)
        if err2:
            return jsonify({"message": err2, "logs": logs}), 404
        
        return jsonify({"count": len(records), "records": records, "logs": logs}), 200
        
    except Exception as e:
        print(f"Erro na requisição: {e}")
        traceback.print_exc()
        return jsonify({"message": f"Erro interno: {e}", "logs": ["Erro interno: " + str(e)]}), 500
