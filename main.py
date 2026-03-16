#!/usr/bin/env python3
# main.py - EBET Aviator + IFRAME EXATO (provider-game-iframe) + PRINT EM TODO PASSO

import os
import sys
import time
import threading
import re
import random
import traceback
import signal
from pathlib import Path
import requests
from flask import Flask, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.support.ui import WebDriverWait

# ================= CONFIG =================
TELEGRAM_TOKEN = "8742776802:AAHSzD1qTwCqMEOdoW9_pT2l5GfmMBWUZQY"
TELEGRAM_CHAT_ID = "7427648935"
PHONE = "857789345"
PASSWORD = "max123ZICO"
HOME_URL = "https://ebet.co.mz/"

app = Flask(__name__)

historico = []
global_history = []
_history_lock = threading.Lock()
_last_telegram = 0

SCREEN_DIR = Path("/tmp/ebet_aviator_steps")
SCREEN_DIR.mkdir(parents=True, exist_ok=True)

def send_telegram_text(msg, throttle=6):
    global _last_telegram
    if time.time() - _last_telegram < throttle:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        _last_telegram = time.time()
    except:
        pass

def screenshot_and_send(driver, label):
    try:
        path = f"/tmp/{int(time.time())}_{label.replace(' ', '_')[:30]}.png"
        driver.save_screenshot(path)
        send_telegram_text(f"📸 {label}")
        print(f"   📸 Screenshot enviado: {label}")
    except Exception as e:
        print(f"   ❌ Falha ao enviar screenshot: {e}")

def print_step(step):
    print(f"\n{'='*85}")
    print(f"🚀 PASSO: {step}")
    print(f"{'='*85}")
    send_telegram_text(f"📍 {step}")

def safe_find_elements(driver, selector):
    for _ in range(5):
        try:
            return driver.find_elements(By.CSS_SELECTOR, selector)
        except:
            time.sleep(0.4)
    return []

def click_aviator_image(driver):
    print("   Procurando imagem do Aviator...")
    for img in safe_find_elements(driver, "img.landing-page__item-image"):
        try:
            src = (img.get_attribute("src") or "").lower()
            if "aviator" in src:
                driver.execute_script("arguments[0].click();", img)
                print("   ✅ Clique na imagem Aviator executado!")
                screenshot_and_send(driver, "Clique Aviator OK")
                return True
        except:
            continue
    print("   ⚠️ Imagem Aviator não encontrada")
    screenshot_and_send(driver, "Falha - Imagem Aviator não encontrada")
    return False

def coletar_historico_dom(driver):
    vals = []
    for el in safe_find_elements(driver, "div.payouts-block div.payout, div.payout"):
        try:
            m = re.search(r"(\d+\.?\d*)", el.text.strip())
            if m:
                vals.append(float(m.group(1)))
        except:
            continue
    return vals

def page_shows_rate_limit(driver):
    try:
        return any(x in driver.page_source.lower() for x in ["rate limit", "too many requests", "429"])
    except:
        return False

def start_driver():
    print_step("Iniciando Chrome Driver")
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
    return webdriver.Chrome(service=service, options=opts)

def iniciar_scraper():
    global historico, global_history
    backoff = 10

    while True:
        driver = None
        try:
            print_step("INICIANDO NOVO CICLO")
            driver = start_driver()

            print_step("1 - Abrindo Home EBET")
            driver.get(HOME_URL)
            time.sleep(8)
            screenshot_and_send(driver, "1 - Home EBET aberta")

            print_step("2 - Clicando Aviator (1ª vez)")
            click_aviator_image(driver)
            time.sleep(5)

            print_step("3 - Fazendo Login")
            try:
                phone = driver.find_element(By.ID, "phone-input")
                phone.clear()
                phone.send_keys(PHONE)
                password = driver.find_element(By.ID, "password-input")
                password.clear()
                password.send_keys(PASSWORD)
                btn = driver.find_element(By.CSS_SELECTOR, "input.btn.btn-primary.btn-session")
                driver.execute_script("arguments[0].click();", btn)
                screenshot_and_send(driver, "3 - Login enviado com sucesso")
                print("✅ Login enviado")
            except Exception as e:
                screenshot_and_send(driver, "3 - Falha no Login")
                print("⚠️ Falha no login:", e)

            time.sleep(8)

            print_step("4 - Clicando Aviator (2ª vez)")
            click_aviator_image(driver)
            time.sleep(6)

            print_step("5 - Entrando no iframe (provider-game-iframe)")
            iframe_el = None
            for f in driver.find_elements(By.TAG_NAME, "iframe"):
                try:
                    src = (f.get_attribute("src") or "").lower()
                    class_name = (f.get_attribute("class") or "").lower()
                    id_name = (f.get_attribute("id") or "").lower()

                    if id_name == "provider-game-iframe" or "spribe-iframe" in class_name or "launch.spribegaming.com" in src:
                        iframe_el = f
                        print("   ✅ IFRAME EXATO ENCONTRADO!")
                        screenshot_and_send(driver, "5 - Iframe encontrado")
                        break
                except:
                    continue

            if iframe_el:
                driver.switch_to.frame(iframe_el)
                print("✅ Entrou no iframe com sucesso!")
                screenshot_and_send(driver, "5 - Entrou no iframe")
            else:
                screenshot_and_send(driver, "5 - Falha - Iframe não encontrado")
                raise RuntimeError("Iframe não localizado")

            print_step("6 - Aguardando e capturando histórico")
            start_time = time.time()
            while time.time() - start_time < 90:
                payouts = safe_find_elements(driver, "div.payouts-block div.payout, div.payout")
                print(f"   Tentativa → {len(payouts)} payouts")
                if len(payouts) > 0:
                    print("✅ PAYOUTS ENCONTRADOS!")
                    screenshot_and_send(driver, "6 - Payouts encontrados")
                    break
                time.sleep(4)

            historico = coletar_historico_dom(driver)
            with _history_lock:
                global_history = historico[:]
            print(f"✅ Histórico inicial carregado: {len(historico)} itens")
            screenshot_and_send(driver, "6 - Histórico inicial OK")

            # LOOP PRINCIPAL
            while True:
                print_step("LOOP - Verificando novo histórico")
                novos = coletar_historico_dom(driver)

                if novos and (not historico or novos[0] != historico[0]):
                    print(f"🔄 NOVO HISTÓRICO! Último: {novos[0]:.2f}x")
                    with _history_lock:
                        for v in novos:
                            if v not in global_history:
                                global_history.insert(0, v)
                        if len(global_history) > 50:
                            global_history = global_history[:50]
                    lista = ", ".join(f"{v:.2f}x" for v in global_history[:20])
                    print(f"   Histórico atualizado: {lista}")
                    send_telegram_text(f"📊 **EBET AVIATOR - ÚLTIMOS 50**\n[{lista}]\nÚltimo: *{global_history[0]:.2f}x*")
                    screenshot_and_send(driver, "Histórico atualizado")
                    historico = novos[:]

                print("⏳ Aguardando próxima verificação (15-25s)...")
                time.sleep(15 + random.uniform(5, 10))

        except Exception as e:
            print(f"❌ ERRO: {type(e).__name__} - {e}")
            traceback.print_exc()
            send_telegram_text(f"🔥 ERRO: {type(e).__name__}")
            screenshot_and_send(driver, "ERRO - Falha geral")
            time.sleep(15)

        finally:
            if driver:
                try:
                    driver.quit()
                    print("🔌 Driver fechado")
                except:
                    pass
            time.sleep(5)


def supervisor_thread():
    while True:
        worker = threading.Thread(target=iniciar_scraper, daemon=True)
        worker.start()
        print("✅ Supervisor: Worker iniciado")
        worker.join()
        print("⚠️ Worker morreu - reiniciando em 10s...")
        time.sleep(10)


@app.route("/api/history")
def api_history():
    with _history_lock:
        return jsonify(global_history)

@app.route("/")
def home():
    return "EBET AVIATOR - IFRAME EXATO + PRINT EM TODO PASSO"

if __name__ == "__main__":
    threading.Thread(target=supervisor_thread, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
