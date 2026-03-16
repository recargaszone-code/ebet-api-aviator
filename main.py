#!/usr/bin/env python3
# main.py - EBET Aviator + LOGS NO CONSOLE + HISTÓRICO 50 + Supervisor

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
from selenium.common.exceptions import (
    StaleElementReferenceException,
    NoSuchElementException,
    WebDriverException,
)
from selenium.webdriver.support.ui import WebDriverWait

# ================= CONFIG =================
TELEGRAM_TOKEN = "8742776802:AAHSzD1qTwCqMEOdoW9_pT2l5GfmMBWUZQY"
TELEGRAM_CHAT_ID = "7427648935"
PHONE = "857789345"
PASSWORD = "max123ZICO"
URL = "https://ebet.co.mz/games/go/spribe?id=aviator"

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
        path = f"/tmp/{int(time.time())}_{label.replace(' ', '_')}.png"
        driver.save_screenshot(path)
        send_telegram_text(f"📸 {label}")
    except:
        pass

def print_step(step):
    print(f"\n{'='*70}")
    print(f"🚀 PASSO: {step}")
    print(f"{'='*70}")
    send_telegram_text(f"📍 {step}")

def safe_find_elements(driver, selector):
    for _ in range(4):
        try:
            return driver.find_elements(By.CSS_SELECTOR, selector)
        except:
            time.sleep(0.3)
    return []

def click_aviator_if_found(driver):   # ← FUNÇÃO QUE FALTAVA
    print("   Procurando imagem do Aviator...")
    for img in safe_find_elements(driver, "img.landing-page__item-image"):
        try:
            src = (img.get_attribute("src") or "").lower()
            alt = (img.get_attribute("alt") or "").lower()
            if "aviator" in src or "aviator" in alt:
                driver.execute_script("arguments[0].click();", img)
                print("   ✅ Clique no Aviator executado!")
                return True
        except:
            continue
    print("   ⚠️ Imagem do Aviator não encontrada")
    return False

def coletar_historico_dom(driver):
    vals = []
    for el in safe_find_elements(driver, "div.payouts-block div.payout"):
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

            print_step("1 - Abrindo URL")
            driver.get(URL)
            time.sleep(8)
            screenshot_and_send(driver, "Página inicial")

            print_step("2 - Clicando Aviator")
            click_aviator_if_found(driver)
            time.sleep(4)

            print_step("3 - Tentando Login")
            try:
                phone = driver.find_element(By.ID, "phone-input")
                phone.send_keys(PHONE)
                password = driver.find_element(By.ID, "password-input")
                password.send_keys(PASSWORD)
                btn = driver.find_element(By.CSS_SELECTOR, "input.btn-session")
                driver.execute_script("arguments[0].click();", btn)
                screenshot_and_send(driver, "Login enviado")
                print("✅ Login enviado com sucesso")
            except:
                print("⚠️ Campos de login não encontrados (talvez já logado)")

            time.sleep(8)

            print_step("4 - Entrando no iframe Spribe")
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
            for f in driver.find_elements(By.TAG_NAME, "iframe"):
                src = (f.get_attribute("src") or "").lower()
                if "spribe" in src or "spribegaming" in src:
                    driver.switch_to.frame(f)
                    print("✅ Entrou no iframe!")
                    break

            print_step("5 - Aguardando payouts aparecerem")
            start_time = time.time()
            while time.time() - start_time < 90:
                if page_shows_rate_limit(driver):
                    print("⚠️ RATE LIMIT detectado no aguardo inicial")
                    time.sleep(15)
                    continue
                if safe_find_elements(driver, "div.payouts-block div.payout"):
                    print("✅ Payouts encontrados!")
                    break
                time.sleep(3)

            print_step("6 - Conectado! Coletando histórico inicial")
            historico = coletar_historico_dom(driver)
            with _history_lock:
                global_history = historico[:]
            print(f"✅ Histórico inicial carregado: {len(historico)} itens")

            # ===================== LOOP PRINCIPAL (15-25s) =====================
            while True:
                print_step("LOOP - Verificando novo histórico (15-25s)")
                
                if page_shows_rate_limit(driver):
                    print("⚠️ RATE LIMIT DETECTADO no loop")
                    sleep_time = backoff + random.uniform(5, 15)
                    print(f"   Dormindo {int(sleep_time)} segundos...")
                    time.sleep(sleep_time)
                    backoff = min(600, backoff * 1.5)
                    continue

                novos = coletar_historico_dom(driver)

                if novos and (not historico or novos[0] != historico[0]):
                    print(f"🔄 NOVO HISTÓRICO DETECTADO! Último: {novos[0]:.2f}x")
                    added = False
                    with _history_lock:
                        for v in novos:
                            if v not in global_history:
                                global_history.insert(0, v)
                                added = True
                        if len(global_history) > 50:
                            global_history = global_history[:50]
                    if added:
                        lista = ", ".join(f"{v:.2f}x" for v in global_history[:20])
                        print(f"   Histórico atualizado (50): {lista}")
                        send_telegram_text(f"📊 **EBET AVIATOR - ÚLTIMOS 50**\n[{lista}]\nÚltimo: *{global_history[0]:.2f}x*")
                        screenshot_and_send(driver, "Histórico atualizado")

                    historico = novos[:]

                print(f"⏳ Aguardando próxima verificação em 15-25 segundos...")
                time.sleep(15 + random.uniform(5, 10))

        except Exception as e:
            print(f"❌ ERRO GERAL: {type(e).__name__} - {e}")
            traceback.print_exc()
            send_telegram_text(f"🔥 ERRO: {type(e).__name__}")
            time.sleep(15)
            backoff = min(600, backoff * 2)

        finally:
            if driver:
                try:
                    driver.quit()
                    print("🔌 Driver fechado com sucesso")
                except:
                    pass
            time.sleep(5)


# ===================== SUPERVISOR =====================
def supervisor_thread():
    while True:
        worker = threading.Thread(target=iniciar_scraper, daemon=True)
        worker.start()
        print("✅ Supervisor: Worker iniciado")
        worker.join()
        print("⚠️ Worker morreu - reiniciando em 10 segundos...")
        time.sleep(10)


@app.route("/api/history")
def api_history():
    with _history_lock:
        return jsonify(global_history)

@app.route("/")
def home():
    return "EBET AVIATOR - LOGS NO CONSOLE ATIVADOS"

if __name__ == "__main__":
    threading.Thread(target=supervisor_thread, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
