# selenium_scraper.py
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from flask import Flask, jsonify, request
from flask_cors import CORS
from bs4 import BeautifulSoup
import json

# --- Configuração do Selenium ---

def create_driver():
    """Cria e configura o WebDriver do Selenium para usar o Chromium em modo headless."""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    try:
        print("Iniciando o driver do Selenium com Chromium...")
        driver = webdriver.Chrome(options=options)
        return driver
    except Exception as e:
        print(f"Erro ao iniciar o WebDriver: {e}")
        # Retornar o erro para que a API possa reportá-lo
        raise RuntimeError(f"Falha ao iniciar o WebDriver: {e}")


def fetch_data_with_selenium(driver, cpf_para_pesquisa):
    """Busca os dados no TCM-GO usando Selenium."""
    url = "https://www.tcmgo.tc.br/site/portal-da-transparencia/consulta-de-contratos-de-pessoal/"
    
    try:
        print(f"Acessando a página para o CPF: {cpf_para_pesquisa}")
        driver.get(url)
        
        import time
        time.sleep(3)

        wait = WebDriverWait(driver, 20)
        
        try:
            iframe = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[src*='consulta-ato-pessoal']")))
        except:
            print("Iframe com src contendo 'consulta-ato-pessoal' não encontrado. Tentando por tag iframe...")
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            print(f"Total de iframes encontrados: {len(iframes)}")
            if iframes:
                iframe = iframes[0]
            else:
                raise Exception("Nenhum iframe encontrado na página")
        
        driver.switch_to.frame(iframe)
        print("Dentro do iframe com sucesso")
        
        cpf_limpo = cpf_para_pesquisa.replace(".", "").replace("-", "")
        cpf_input = wait.until(EC.presence_of_element_located((By.ID, "pesquisaAtos:cpf")))
        cpf_input.clear()
        cpf_input.send_keys(cpf_limpo)
        print(f"CPF inserido: {cpf_limpo}")
        
        search_button = wait.until(EC.element_to_be_clickable((By.ID, "pesquisaAtos:abrirAtos")))
        driver.execute_script("arguments[0].click();", search_button)
        print("Botão clicado")
        
        time.sleep(5)
        
        # Aguarda a tabela com tbody aparecer
        try:
            wait.until(EC.visibility_of_element_located((By.TAG_NAME, "tbody")))
            print("Tbody encontrado - resultados carregados")
        except:
            print("Aviso: tbody não encontrado após busca")
        
        # Procura por uma tabela que contenha dados reais
        tables = driver.find_elements(By.TAG_NAME, "table")
        print(f"Total de tabelas encontradas: {len(tables)}")
        
        result_table_html = None
        
        for idx, table in enumerate(tables):
            tbody = table.find_element(By.TAG_NAME, "tbody")
            rows = tbody.find_elements(By.TAG_NAME, "tr")
            print(f"Tabela {idx}: {len(rows)} linhas")
            
            # Pega a tabela que tem mais de 0 linhas de dados
            if len(rows) > 0:
                # Verifica se não é a mensagem "Nenhum registro encontrado"
                first_row_text = rows[0].text
                if "Nenhum registro" not in first_row_text.lower():
                    result_table_html = table.get_attribute('outerHTML')
                    print(f"Tabela com dados encontrada (índice {idx})")
                    print(f"Primeiras 500 caracteres do HTML: {result_table_html[:500]}")
                    break
        
        if not result_table_html:
            print("Nenhuma tabela com dados foi encontrada")
            return None, "Nenhum registro encontrado para o CPF informado."
        
        print("Tabela de resultados encontrada com sucesso.")
        return result_table_html, None

    except TimeoutException as e:
        print(f"Tempo de espera excedido: {e}")
        return None, "A busca não retornou resultados a tempo. Verifique o CPF ou tente novamente."
    except Exception as e:
        print(f"Ocorreu um erro durante a raspagem com Selenium: {e}")
        import traceback
        traceback.print_exc()
        return None, f"Erro inesperado durante a raspagem: {e}"
    finally:
        driver.switch_to.default_content()


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
        print("Aviso: tbody não encontrado no HTML")
        return [], "tbody não encontrado"
    
    rows = tbody.find_all('tr')
    print(f"Linhas encontradas no tbody: {len(rows)}")
    
    if not rows:
        return [], "Nenhuma linha encontrada na tabela"
    
    # Verifica a primeira linha
    if len(rows) == 1:
        first_row_text = rows[0].text.strip()
        print(f"Texto da primeira (única) linha: '{first_row_text}'")
        if "Nenhum registro" in first_row_text.lower() or first_row_text == "":
            return [], None
    
    for idx, row in enumerate(rows):
        cells = row.find_all('td')
        print(f"Linha {idx}: {len(cells)} células encontradas")
        
        # Imprime o conteúdo de cada célula
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

    print(f"Total de registros extraídos: {len(data)}")
    return data, None

# --- API Flask ---

app = Flask(__name__)
CORS(app)

@app.route('/api/buscar-registro-selenium', methods=['POST'])
def buscar_registro_selenium():
    """Endpoint para receber a requisição de busca e retornar os dados usando Selenium."""
    if not request.is_json:
        return jsonify({"error": "O corpo da requisição deve ser JSON."}), 415

    data = request.json
    cpf = data.get('cpf')

    if not cpf:
        return jsonify({"error": "O campo 'cpf' é obrigatório."}), 400

    driver = None
    try:
        driver = create_driver()
        html_content, error = fetch_data_with_selenium(driver, cpf)
        
        if error:
            # Erros de `fetch_data` já são formatados para o cliente.
            return jsonify({"error": error}), 500
        
        if not html_content:
            return jsonify({"message": "Nenhum conteúdo HTML foi retornado da busca."}), 404

        extracted_data, error_extract = extract_data_from_html(html_content)
        
        if error_extract:
            return jsonify({"error": f"Erro ao processar os dados: {error_extract}"}), 500

        if extracted_data:
            tipos_desejados = ["Admissao", "Concursado"]
            dados_filtrados = [item for item in extracted_data if item.get('Tipo de Contrato') in tipos_desejados]
            
            if dados_filtrados:
                print(f"Encontrados {len(dados_filtrados)} registros de admissão para o CPF.")
                # Retorna o primeiro registro de admissão, como no app.py original
                return jsonify(dados_filtrados[0])
            else:
                print("Nenhum registro de admissão (Admissao/Concursado) encontrado.")
                return jsonify({"message": "Nenhum registro de admissão do tipo 'Admissao' ou 'Concursado' foi encontrado."}), 404
        else:
            print("Nenhum registro encontrado para o CPF.")
            return jsonify({"message": "Nenhum registro encontrado para o CPF informado."}), 404

    except RuntimeError as e:
        # Captura o erro específico de falha ao iniciar o driver
        return jsonify({"error": str(e)}), 503 # Service Unavailable
    except Exception as e:
        # Captura outras exceções inesperadas
        print(f"Erro fatal na API: {e}")
        return jsonify({"error": f"Erro interno no servidor: {e}"}), 500
    finally:
        if driver:
            print("Fechando o driver do Selenium.")
            driver.quit()

# Para rodar localmente para teste:
# if __name__ == '__main__':
#    app.run(debug=True, port=5001)
