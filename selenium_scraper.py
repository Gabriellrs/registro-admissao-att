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

    async with async_playwright() as p:
        # args para ambiente container
        launch_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        browser = await p.chromium.launch(headless=True, args=launch_args)
        # definir user-agent no context para reduzir detecção
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        context = await browser.new_context(user_agent=user_agent, viewport={"width":1920, "height":1080})
        page = await context.new_page()
        try:
            print(f"Acessando a página para o CPF: {cpf_para_pesquisa}")

            # Tentativa principal (networkidle) com timeout maior
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
                print("page.goto networkidle OK")
            except Exception as e_net:
                print(f"networkidle timeout ou erro: {e_net}. Tentando domcontentloaded...")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    print("page.goto domcontentloaded OK")
                except Exception as e_dom:
                    print(f"domcontentloaded também falhou: {e_dom}. Tentando goto sem espera (curto timeout)...")
                    try:
                        await page.goto(url, timeout=15000)
                        print("page.goto sem wait_until OK")
                    except Exception as e_final:
                        print(f"Falha final ao navegar: {e_final}")
                        # tenta salvar snapshot mínimo para debug
                        try:
                            html_preview = await page.content()
                            with open("/tmp/page_debug_on_goto_fail.html", "w", encoding="utf-8") as f:
                                f.write(html_preview)
                            print("Salvo /tmp/page_debug_on_goto_fail.html")
                        except Exception as se:
                            print(f"Erro salvando debug HTML: {se}")
                        return None, f"Timeout ao acessar a página: {e_final}"

            # localizar iframe (fallbacks)
            print("Procurando iframe...")
            iframe_handle = await page.query_selector("iframe[src*='consulta-ato-pessoal']")
            if not iframe_handle:
                iframes = await page.query_selector_all("iframe")
                print(f"iframes encontrados: {len(iframes)}")
                if iframes:
                    iframe_handle = iframes[0]
                else:
                    # salva HTML para debug
                    page_html = await page.content()
                    with open("/tmp/page_no_iframe.html", "w", encoding="utf-8") as f:
                        f.write(page_html)
                    print("Iframe não encontrado. Debug salvo em /tmp/page_no_iframe.html")
                    return None, "Iframe não encontrado na página principal."

            frame = await iframe_handle.content_frame()
            if not frame:
                page_html = await page.content()
                with open("/tmp/page_iframe_no_frame.html", "w", encoding="utf-8") as f:
                    f.write(page_html)
                print("Não foi possível obter content_frame do iframe. Debug salvo em /tmp/page_iframe_no_frame.html")
                return None, "Não foi possível acessar o conteúdo do iframe."

            print("Dentro do iframe com sucesso")
            cpf_limpo = cpf_para_pesquisa.replace(".", "").replace("-", "")
            print(f"CPF inserido: {cpf_limpo}")

            # preencher e submeter
            try:
                await frame.fill("#pesquisaAtos\\:cpf", cpf_limpo, timeout=5000)
                await frame.click("#pesquisaAtos\\:abrirAtos", timeout=5000)
            except Exception as e_fill:
                print(f"Erro ao preencher/clicar no iframe: {e_fill}")
                # salva iframe para debug
                try:
                    frame_html_dbg = await frame.content()
                    with open("/tmp/iframe_after_fill_fail.html", "w", encoding="utf-8") as f:
                        f.write(frame_html_dbg)
                    print("Salvo /tmp/iframe_after_fill_fail.html")
                except Exception as se:
                    print(f"Erro salvando iframe debug: {se}")
                return None, f"Falha ao interagir com o formulário do iframe: {e_fill}"

            # aguardar resultados (tenta múltiplos timeouts)
            await page.wait_for_timeout(4000)
            try:
                await frame.wait_for_selector("table tbody tr", timeout=15000)
                print("Tabela encontrada com linhas")
            except Exception:
                print("Tabela não apareceu dentro do timeout; vou salvar HTML de debug e tentar extrair qualquer tabela disponível")

            # salvar HTMLs de debug
            try:
                frame_html = await frame.content()
                page_html = await page.content()
                with open("/tmp/iframe_debug.html", "w", encoding="utf-8") as f:
                    f.write(frame_html)
                with open("/tmp/page_debug.html", "w", encoding="utf-8") as f:
                    f.write(page_html)
                print("HTML de debug salvo em /tmp/iframe_debug.html e /tmp/page_debug.html")
            except Exception as e_save:
                print(f"Erro salvando HTML de debug: {e_save}")

            # tentar extrair a tabela (se existir)
            table_html = ""
            try:
                table_html = await frame.inner_html("table")
            except Exception:
                table_html = ""

            if not table_html or table_html.strip() == "":
                return None, "Tabela de resultados não encontrada no HTML processado. Verifique /tmp/iframe_debug.html e /tmp/page_debug.html para debug."

            print("Tabela extraída com sucesso")
            return table_html, None

        except Exception as e:
            print(f"Erro Playwright inesperado: {e}")
            traceback.print_exc()
            return None, f"Erro ao buscar dados: {e}"
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
