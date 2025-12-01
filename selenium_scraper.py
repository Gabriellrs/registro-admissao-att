# selenium_scraper.py
import os
import time
import traceback
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from flask import Flask, jsonify, request
from flask_cors import CORS
from bs4 import BeautifulSoup
from selenium.webdriver.chrome.service import Service

# --- Configuração do Selenium ---

def create_driver():
    """Cria e configura o WebDriver do Selenium para usar o Chromium em modo headless.
    Usa webdriver-manager se disponível e tenta baixar a versão do chromedriver
    correspondente à versão do Chromium detectada."""
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        use_wdm = True
    except ModuleNotFoundError:
        ChromeDriverManager = None
        use_wdm = False

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

    chrome_bin = os.environ.get("CHROME_BIN") or os.environ.get("GOOGLE_CHROME_BIN") or os.environ.get("CHROME_PATH") or "/usr/bin/chromium"
    if chrome_bin:
        options.binary_location = chrome_bin
        print(f"Usando binário do Chrome em: {chrome_bin}")

    def detect_chrome_version(bin_path):
        try:
            import subprocess
            out = subprocess.check_output([bin_path, "--version"], stderr=subprocess.STDOUT).decode(errors="ignore")
            # ex: "Chromium 142.0.7444.175"
            import re
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
            if m:
                return m.group(1)
            m2 = re.search(r"(\d+)\.", out)
            if m2:
                return m2.group(1)
        except Exception as e:
            print(f"Não foi possível detectar versão do Chrome: {e}")
        return None

    try:
        print("Iniciando o driver do Selenium com Chromium...")
        if use_wdm:
            # tenta detectar versão do Chromium e instalar driver correspondente
            chrome_ver = detect_chrome_version(options.binary_location)
            try:
                if chrome_ver:
                    print(f"Versão do Chromium detectada: {chrome_ver}. Tentando baixar chromedriver correspondente.")
                    service = Service(ChromeDriverManager(version=chrome_ver).install())
                else:
                    print("Versão do Chromium não detectada. Baixando chromedriver padrão.")
                    service = Service(ChromeDriverManager().install())
            except Exception as e:
                print(f"Falha ao instalar chromedriver específico: {e}. Tentando instalação padrão.")
                service = Service(ChromeDriverManager().install())
        else:
            chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
            if chromedriver_path:
                service = Service(chromedriver_path)
                print(f"Usando chromedriver em: {chromedriver_path}")
            else:
                service = Service()  # espera chromedriver no PATH
                print("webdriver-manager não instalado: esperando chromedriver no PATH ou defina CHROMEDRIVER_PATH")

        driver = webdriver.Chrome(service=service, options=options)
        try:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            })
        except Exception:
            pass
        return driver
    except Exception as e:
        print(f"Erro ao iniciar o WebDriver: {e}")
        raise RuntimeError(
            "Falha ao iniciar o WebDriver. Instale 'webdriver-manager' no requirements.txt ou forneça chromedriver via CHROMEDRIVER_PATH ou PATH."
        )


def fetch_data_with_selenium(driver, cpf_para_pesquisa):
    """Busca os dados no TCM-GO usando Selenium."""
    url = "https://www.tcmgo.tc.br/site/portal-da-transparencia/consulta-de-contratos-de-pessoal/"
    try:
        print(f"Acessando a página para o CPF: {cpf_para_pesquisa}")
        driver.get(url)
        time.sleep(2)
        wait = WebDriverWait(driver, 20)

        # localizar iframe
        try:
            iframe = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[src*='consulta-ato-pessoal']")))
        except:
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

        time.sleep(4)
        try:
            wait.until(EC.visibility_of_element_located((By.TAG_NAME, "tbody")))
            print("Tbody encontrado - resultados carregados")
        except:
            print("Aviso: tbody não detectado visível, continuando...")

        tables = driver.find_elements(By.TAG_NAME, "table")
        print(f"Total de tabelas encontradas: {len(tables)}")

        result_table_html = None
        for idx, table in enumerate(tables):
            try:
                tbody = table.find_element(By.TAG_NAME, "tbody")
                rows = tbody.find_elements(By.TAG_NAME, "tr")
                print(f"Tabela {idx}: {len(rows)} linhas")
                if len(rows) > 0:
                    first_row_text = rows[0].text
                    if "nenhum registro" not in first_row_text.lower() and first_row_text.strip() != "":
                        result_table_html = table.get_attribute('outerHTML')
                        break
            except Exception:
                continue

        if not result_table_html:
            print("Nenhuma tabela com dados foi encontrada")
            try:
                iframe_html = driver.page_source
                debug_path = "/tmp/iframe_debug.html"
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(iframe_html)
                print(f"HTML do iframe salvo para debug em: {debug_path}")
            except Exception as e:
                print(f"Não foi possível salvar HTML de debug: {e}")
            return None, "Nenhum registro encontrado para o CPF informado."

        print("Tabela de resultados encontrada com sucesso.")
        return result_table_html, None

    except TimeoutException as e:
        print(f"Tempo de espera excedido: {e}")
        return None, "A busca não retornou resultados a tempo. Verifique o CPF ou tente novamente."
    except Exception as e:
        print(f"Ocorreu um erro durante a raspagem com Selenium: {e}")
        traceback.print_exc()
        return None, f"Erro inesperado durante a raspagem: {e}"
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass


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
            # tenta mapping por posição mesmo se tamanhos divergirem (fallback)
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
    payload = request.get_json(force=True)
    cpf = payload.get("cpf") if payload else None
    if not cpf:
        return jsonify({"message": "CPF não informado."}), 400

    driver = None
    try:
        driver = create_driver()
        html, err = fetch_data_with_selenium(driver, cpf)
        if err:
            return jsonify({"message": err}), 404
        records, err2 = extract_data_from_html(html)
        if err2:
            return jsonify({"message": err2}), 404
        return jsonify({"count": len(records), "records": records}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"message": f"Erro interno: {e}"}), 500
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

# Para rodar localmente para teste:
# if __name__ == '__main__':
#    app.run(debug=True, port=5001)
