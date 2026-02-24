import os
import time
import json
import asyncio
import traceback
import re
import unicodedata
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- FUNÇÕES DE UTILIDADE ---

def limpar_nome(nome):
    """
    Limpa o nome: remove acentos, remove o Ç/ç completamente, 
    remove caracteres especiais, tira espaços extras e converte para maiúsculas.
    """
    if not nome:
        return nome
        
    # 0. Remove o 'Ç' e 'ç' completamente antes de qualquer conversão
    nome_sem_cedilha = nome.replace('Ç', '').replace('ç', '')
        
    # 1. Normaliza a string (remove os acentos do restante do texto)
    nome_normalizado = unicodedata.normalize('NFKD', nome_sem_cedilha).encode('ASCII', 'ignore').decode('utf-8')
    
    # 2. Remove caracteres especiais (mantém apenas letras de A-Z e espaços)
    nome_apenas_letras = re.sub(r'[^a-zA-Z\s]', '', nome_normalizado)
    
    # 3. Remove espaços duplicados no meio, espaços nas pontas e joga para maiúsculo
    nome_limpo = re.sub(r'\s+', ' ', nome_apenas_letras).strip().upper()
    
    return nome_limpo

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
        
        # Filtro de Município (APENAS SE PASSADO - geralmente agora é filtrado via Dropdown)
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

async def select_dropdown_fuzzy(frame, selector_xpath, text_to_match):
    """
    Seleciona uma opção no dropdown que contenha o texto_to_match (fuzzy/parcial).
    Retorna True se achou e selecionou, False caso contrário.
    """
    if not text_to_match:
        return False
        
    try:
        # Pega todas as opções do select
        # XPath para o select deve ser passado corretamente
        select_handle = await frame.query_selector(f"xpath={selector_xpath}")
        if not select_handle:
            print(f"Dropdown não encontrado: {selector_xpath}")
            return False
            
        options = await select_handle.query_selector_all("option")
        best_match_value = None
        best_match_text = ""
        
        target = text_to_match.lower()
        
        # Estrategia 1: Match Exato ou Contains Forte
        for opt in options:
            text = await opt.text_content()
            val = await opt.get_attribute("value")
            
            if not text or val == "0" or val == "-1" or val == "": # Ignora opções neutras
                continue
                
            t_lower = text.lower()
            
            # Se o texto alvo contiver o texto da opção (ex: "ITAPURANGA PREVGO" contem "ITAPURANGA")
            if t_lower in target or target in t_lower:
                best_match_value = val
                best_match_text = text
                break
        
        if best_match_value:
            print(f"Selecionando Município: '{best_match_text}' (Match com '{text_to_match}')")
            await select_handle.select_option(value=best_match_value)
            return True
        else:
            print(f"Nenhuma opção de município encontrada para '{text_to_match}'")
            return False
            
    except Exception as e:
        print(f"Erro ao selecionar dropdown: {e}")
        return False

# --- CORE DO SCRAPER ---

async def fetch_data_with_playwright(cpf=None, nome=None, municipio_filtro=None, cargo_filtro=None):
    """Busca dados usando Playwright com estratégias de fallback e suporte a paginação/filtros."""
    
    # Aplica a limpeza no nome logo no início
    if nome:
        nome = limpar_nome(nome)
        
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
                cpf_limpo = cpf.replace(".", "").replace("-", "")
                log(f"Preenchendo CPF: {cpf_limpo}")
                await frame.fill(selector_cpf, cpf_limpo)
            elif nome:
                log(f"Preenchendo Nome: {nome}")
                await frame.fill(selector_nome, nome)
                await frame.press(selector_nome, "Tab") # TRUQUE DO TAB
                await page.wait_for_timeout(500)
            
            # --- SELEÇÃO DE MUNICÍPIO (DROPDOWN FUZZY) ---
            # SE TIVER CPF, IGNORA O MUNICÍPIO (Busca Global)
            if municipio_filtro and not cpf:
                log(f"Tentando selecionar município: {municipio_filtro}")
                xpath_mun = "//label[contains(text(), 'Município')]/following-sibling::select"
                match_found = await select_dropdown_fuzzy(frame, xpath_mun, municipio_filtro)
                if match_found:
                    # Espera um pouco caso o select dispare AJAX
                    await page.wait_for_timeout(2000)
                else:
                    log("WARN: Município não encontrado no dropdown. A busca prosseguirá SEM filtro de município.")
            elif cpf:
                 log("Busca por CPF detectada: Ignorando filtro de município (Busca Global).")
            
            # --- LOOP DE PREENCHIMENTO ROBUSTO ---
            # O PrimeFaces pode limpar os campos após o AJAX do município. 
            # Vamos garantir que o campo está preenchido ANTES de clicar.
            
            max_retries_input = 3
            for attempt in range(max_retries_input):
                log(f"--- Tentativa de Preenchimento {attempt+1}/{max_retries_input} ---")
                
                # 1. Preenche
                if cpf:
                    cpf_limpo = cpf.replace(".", "").replace("-", "")
                    log(f"  Preenchendo CPF: '{cpf_limpo}'")
                    await frame.fill(selector_cpf, cpf_limpo)
                elif nome:
                    log(f"  Preenchendo Nome: '{nome}'")
                    await frame.fill(selector_nome, nome)
                    await frame.press(selector_nome, "Tab")
                
                log("  Aguardando 500ms para persistência...")
                await page.wait_for_timeout(500)
                
                # 2. Verifica se o valor "pegou"
                if cpf:
                    val_check = await frame.input_value(selector_cpf)
                    target_check = cpf_limpo
                else:
                    val_check = await frame.input_value(selector_nome)
                    target_check = nome
                
                log(f"  Verificação -> Valor no Input: '{val_check}' | Valor Esperado: '{target_check}'")
                
                if target_check in val_check:
                    log("  >> SUCESSO: O campo manteve o valor correto.")
                    break
                else:
                    log(f"  >> FALHA: O valor diferiu ou sumiu. Tentando novamente...")
                    await page.wait_for_timeout(1000)

            # --- CAPTURA ELEMENTO ANTIGO (PARA VERIFICAR REFRESH) ---
            old_table = await frame.query_selector("tbody#form\\:mytable_data")

            log("Clicando em Consultar...")
            await frame.click(selector_btn)
            
            # --- ESPERA INTELIGENTE (STALE ELEMENT) ---
            # O PrimeFaces destroi e recria a tabela. Se esperarmos o antigo sumir, garantimos o refresh.
            if old_table:
                try:
                    log("Aguardando tabela antiga ser removida (AJAX)...")
                    # 'hidden' abrange detached ou invisivel
                    await old_table.wait_for_element_state("hidden", timeout=30000)
                except:
                    log("WARN: Tabela antiga não desapareceu ou timeout. O AJAX pode ter falhado ou sido muito rápido.")
            
            # Espera a NOVA tabela aparecer
            log("Aguardando nova tabela de resultados...")
            await frame.wait_for_selector("tbody#form\\:mytable_data", state="visible", timeout=30000)
            
            # Espera extra de segurança para renderização final
            await page.wait_for_timeout(2000)

            # --- PAGINAÇÃO E COLETA ---
            pagina = 1
            while True:
                log(f"Lendo página {pagina}...")
                
                # A tabela já deve estar visivel pelo wait acima
                # Mas mantemos um check rapido
                try:
                    await frame.wait_for_selector("tbody#form\\:mytable_data", timeout=5000)
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
            # OBS: Se selecionamos o município no Dropdown, não precisamos filtrar de novo aqui, exceto se quiser dupla segurança.
            # Vamos manter apenas Cargo, para não filtrar excessivamente caso o nome da cidade tenha "match parcial".
            if cargo_filtro:
                count_before = len(all_records)
                log(f"Aplicando filtros pós-busca: Cargo='{cargo_filtro}'")
                all_records = filter_records(all_records, municipio=None, cargo=cargo_filtro)
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
