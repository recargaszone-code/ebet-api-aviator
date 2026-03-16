#!/usr/bin/env python3
# main.py - EBET Aviator SIMPLES + ACUMULADO DIFERENTE (só adiciona novo no topo, tira antigo)

import os
import time
import threading
import re
import random
import traceback
import requests
from flask import Flask, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ================= CONFIG =================
TELEGRAM_TOKEN = "8742776802:AAHSzD1qTwCqMEOdoW9_pT2l5GfmMBWUZQY"
TELEGRAM_CHAT_ID = "7427648935"
PHONE = "857789345"
PASSWORD = "max123ZICO"
URL = "https://ebet.co.mz/games/go/spribe?id=aviator"

app = Flask(__name__)

historico_atual = []      # o que está na tela agora (snapshot)
historico_acumulado = []  # acumula até 50 (novo no topo, remove antigo do final)
_last_telegram = 0

def send_telegram_text(msg, throttle=20):
    global _last_telegram
    if time.time() - _last_telegram < throttle:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
        _last_telegram = time.time()
    except:
        pass

def screenshot_and_send(driver, label):
    if "Conectado" in label or "Erro" in label:
        try:
            path = f"/tmp/{int(time.time())}_{label}.png"
            driver.save_screenshot(path)
            send_telegram_text(f"📸 {label}")
        except:
            pass

def print_step(step):
    print(f"\n{'='*70}")
    print(f"🚀 {step}")
    print(f"{'='*70}")

def coletar_historico(driver):
    vals = []
    for el in driver.find_elements(By.CSS_SELECTOR, "div.payout"):
        try:
            txt = el.text.strip()
            m = re.search(r"(\d+\.?\d*)", txt)
            if m:
                vals.append(float(m.group(1)))
        except:
            continue
    return vals

def start_driver():
    print_step("Iniciando Driver")
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    if os.path.exists("/usr/bin/chromium"):
        opts.binary_location = "/usr/bin/chromium"
    service = Service("/usr/bin/chromedriver") if os.path.exists("/usr/bin/chromedriver") else Service()
    driver = webdriver.Chrome(service=service, options=opts)
    return driver

def iniciar_scraper():
    global historico_atual, historico_acumulado
    backoff = 20

    while True:
        driver = None
        try:
            print_step("Novo ciclo")
            driver = start_driver()
            wait = WebDriverWait(driver, 60)

            print_step("Abrindo URL")
            driver.get(URL)
            time.sleep(random.uniform(8, 12))

            print_step("Clicando Aviator 1")
            try:
                imgs = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "img.landing-page__item-image")))
                for img in imgs:
                    if "aviator" in (img.get_attribute("src") or "").lower():
                        driver.execute_script("arguments[0].click();", img)
                        print("   Clique 1 OK")
                        break
            except:
                print("   Falha clique 1")

            time.sleep(random.uniform(5, 10))

            print_step("Login")
            try:
                phone = wait.until(EC.presence_of_element_located((By.ID, "phone-input")))
                phone.clear()
                phone.send_keys(PHONE)
                password = driver.find_element(By.ID, "password-input")
                password.clear()
                password.send_keys(PASSWORD)
                btn = driver.find_element(By.CSS_SELECTOR, "input.btn-session")
                driver.execute_script("arguments[0].click();", btn)
                print("   Login OK")
            except:
                print("   Login pulado")

            time.sleep(random.uniform(8, 15))

            print_step("Clicando Aviator 2")
            try:
                imgs = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "img.landing-page__item-image")))
                for img in imgs:
                    if "aviator" in (img.get_attribute("src") or "").lower():
                        driver.execute_script("arguments[0].click();", img)
                        print("   Clique 2 OK")
                        break
            except:
                print("   Falha clique 2")

            time.sleep(random.uniform(10, 20))

            print_step("Iframe externo")
            try:
                iframe1 = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[src*='spribe']")))
                driver.switch_to.frame(iframe1)
                print("   Iframe externo OK")
            except:
                print("   Falha iframe externo")

            print_step("Iframe interno")
            try:
                iframe2 = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[src*='spribegaming']")))
                driver.switch_to.frame(iframe2)
                print("   ✅ Entrou no Aviator!")
                screenshot_and_send(driver, "Conectado ao jogo")
            except:
                print("   Falha iframe interno")
                raise

            print_step("Aguardando histórico")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.payout")))
            print("   Histórico apareceu!")

            historico_atual = coletar_historico(driver)
            historico_acumulado = historico_atual[:]  # inicia acumulado com o atual
            print(f"   Histórico atual: {historico_atual[:10]}...")

            # LOOP PRINCIPAL - simples e lento
            while True:
                novos = coletar_historico(driver)

                if novos and (not historico_atual or novos[0] != historico_atual[0]):
                    novo_valor = novos[0]
                    print(f"   NOVO VALOR DETECTADO: {novo_valor:.2f}x")

                    # Estratégia diferente: adiciona só o novo no topo, remove antigo se >50
                    with _history_lock:
                        if not historico_acumulado or novo_valor != historico_acumulado[0]:
                            historico_acumulado.insert(0, novo_valor)
                            if len(historico_acumulado) > 50:
                                historico_acumulado.pop()  # remove o último (mais antigo)

                    # Envia só os últimos 10 + o novo
                    lista = ", ".join(f"{v:.2f}x" for v in historico_acumulado[:10])
                    send_telegram_text(f"📊 EBET AVIATOR\n[{lista}]\nNovo: *{novo_valor:.2f}x*", throttle=20)
                    screenshot_and_send(driver, "Novo resultado")

                    historico_atual = novos[:]

                time.sleep(random.uniform(20, 40))  # lento pra evitar ban

        except Exception as e:
            print(f"❌ ERRO: {type(e).__name__} - {e}")
            traceback.print_exc()
            send_telegram_text(f"🔥 ERRO: {type(e).__name__}")
            time.sleep(40 + random.uniform(0, 30))

        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
            time.sleep(15)

@app.route("/api/history")
def api_history():
    return jsonify(historico_acumulado)

@app.route("/")
def home():
    return "EBET AVIATOR - SIMPLES + ACUMULADO DIFERENTE"

if __name__ == "__main__":
    threading.Thread(target=iniciar_scraper, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
