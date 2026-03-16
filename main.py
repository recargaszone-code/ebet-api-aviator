#!/usr/bin/env python3
# main.py - EBET Aviator com proteção contra rate-limit + HISTÓRICO ACUMULADO ATÉ 50
# + supervisor robusto + polling a cada 15-25s + heartbeat vivo a cada 30s

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
    TimeoutException,
    StaleElementReferenceException,
    NoSuchElementException,
    WebDriverException,
)
from selenium.webdriver.support.ui import WebDriverWait

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = "8742776802:AAHSzD1qTwCqMEOdoW9_pT2l5GfmMBWUZQY"
TELEGRAM_CHAT_ID = "7427648935"
PHONE = "857789345"
PASSWORD = "max123ZICO"
URL = "https://ebet.co.mz/games/go/spribe?id=aviator"

app = Flask(__name__)

# estado compartilhado
historico = []           # snapshot atual
global_history = []      # acumula até 50
_history_lock = threading.Lock()
_last_telegram = 0

# Supervisor params
SUPERVISOR_BACKOFF_BASE = 5
SUPERVISOR_BACKOFF_MAX = 300
RESTART_WINDOW_SECONDS = 300
RESTART_THRESHOLD = 8

SCREEN_DIR = Path("/tmp/ebet_aviator_steps")
SCREEN_DIR.mkdir(parents=True, exist_ok=True)

def send_telegram_text(msg, throttle_seconds=6):
    global _last_telegram
    if time.time() - _last_telegram < throttle_seconds:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=15,
        )
        _last_telegram = time.time()
    except:
        pass

def send_telegram_photo(path, caption=""):
    global _last_telegram
    if time.time() - _last_telegram < 30:
        return
    try:
        with open(path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": f},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                timeout=30,
            )
        _last_telegram = time.time()
    except:
        pass

def save_screenshot(driver, label):
    try:
        fname = f"{int(time.time())}_{abs(hash(label))%10000}.png"
        path = SCREEN_DIR / fname
        driver.save_screenshot(str(path))
        return str(path)
    except:
        return None

def screenshot_and_send(driver, label):
    p = save_screenshot(driver, label)
    if p:
        send_telegram_photo(p, caption=label)

def safe_find_elements(driver, selector, max_retries=4, sleep_between=0.3):
    for _ in range(max_retries):
        try:
            return driver.find_elements(By.CSS_SELECTOR, selector)
        except StaleElementReferenceException:
            time.sleep(sleep_between)
            continue
        except:
            time.sleep(sleep_between)
    return []

def click_aviator_if_found(driver):
    for img in safe_find_elements(driver, "img.landing-page__item-image"):
        try:
            src = (img.get_attribute("src") or "").lower()
            alt = (img.get_attribute("alt") or "").lower()
            if "aviator" in src or "aviator" in alt:
                driver.execute_script("arguments[0].click();", img)
                return True
        except:
            continue
    return False

def coletar_historico_dom(driver):
    vals = []
    for el in safe_find_elements(driver, "div.payouts-block div.payout"):
        try:
            m = re.search(r"(\d+(\.\d+)?)", el.text.strip())
            if m:
                vals.append(float(m.group(1)))
        except:
            continue
    return vals

def page_shows_rate_limit(driver):
    try:
        body = driver.page_source.lower()
        checks = ["rate limit", "too many requests", "429", "rate-limited", "try again later"]
        return any(token in body for token in checks)
    except:
        return False

def start_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")

    if os.path.exists("/usr/bin/chromium"):
        opts.binary_location = "/usr/bin/chromium"
    service = Service("/usr/bin/chromedriver") if os.path.exists("/usr/bin/chromedriver") else Service()

    driver = webdriver.Chrome(service=service, options=opts)

    # Anti-detection
    try:
        stealth = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
        """
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth})
    except:
        pass
    return driver

def iniciar_scraper():
    global historico, global_history
    base_backoff = 10
    max_backoff = 600
    backoff = base_backoff

    while True:
        driver = None
        falhas_consecutivas = 0
        MAX_FALHAS = 6

        try:
            send_telegram_text("🟢 Iniciando EBET Aviator (supervisor + histórico 50)...")
            driver = start_driver()
            wait = WebDriverWait(driver, 30)

            driver.get(URL)
            time.sleep(8)
            screenshot_and_send(driver, "Página inicial aberta")

            click_aviator_if_found(driver)
            time.sleep(4)

            # Login
            try:
                phone = driver.find_element(By.ID, "phone-input")
                phone.clear()
                phone.send_keys(PHONE)
                password = driver.find_element(By.ID, "password-input")
                password.clear()
                password.send_keys(PASSWORD)
                btn = driver.find_element(By.CSS_SELECTOR, "input.btn-session")
                driver.execute_script("arguments[0].click();", btn)
                screenshot_and_send(driver, "Login enviado")
            except:
                pass

            time.sleep(8)
            click_aviator_if_found(driver)
            time.sleep(5)

            # Trocar aba + iframes
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])

            for f in driver.find_elements(By.TAG_NAME, "iframe"):
                src = (f.get_attribute("src") or "").lower()
                if "spribe" in src and "launch" not in src:
                    driver.switch_to.frame(f)
                    time.sleep(3)
                    break

            for f in driver.find_elements(By.TAG_NAME, "iframe"):
                src = (f.get_attribute("src") or "").lower()
                if "spribegaming" in src or "launch.spribegaming" in src:
                    driver.switch_to.frame(f)
                    time.sleep(4)
                    break

            # Aguarda payouts iniciais
            total_wait_start = time.time()
            while True:
                if page_shows_rate_limit(driver):
                    sleep_time = min(max_backoff, backoff) + random.uniform(5, 15)
                    send_telegram_text(f"⚠️ Rate limit detectado. Dormindo {int(sleep_time)}s")
                    time.sleep(sleep_time)
                    backoff = min(max_backoff, backoff * 1.6)
                    if falhas_consecutivas >= MAX_FALHAS:
                        raise RuntimeError("Rate limit persistente")
                    continue

                if safe_find_elements(driver, "div.payouts-block div.payout"):
                    break

                if time.time() - total_wait_start > 120:
                    raise RuntimeError("Não conseguiu conectar após 120s")

                time.sleep(3)

            # Sucesso inicial
            send_telegram_text("🚀 EBET Aviator conectado com sucesso!")
            screenshot_and_send(driver, "Dentro do jogo (payouts detectados)")

            historico = coletar_historico_dom(driver)
            with _history_lock:
                global_history = historico[:]

            # ====================== LOOP PRINCIPAL (a cada 15-25s) ======================
            while True:
                if page_shows_rate_limit(driver):
                    falhas_consecutivas += 1
                    sleep_time = min(max_backoff, backoff) + random.uniform(5, 15)
                    send_telegram_text(f"Rate limit detectado no loop ({falhas_consecutivas}/{MAX_FALHAS}) — dormindo {int(sleep_time)}s")
                    time.sleep(sleep_time)
                    backoff = min(max_backoff, backoff * 1.6)

                    if falhas_consecutivas >= MAX_FALHAS:
                        raise RuntimeError("Rate limit persistente - reiniciando worker")
                    continue

                falhas_consecutivas = 0
                backoff = base_backoff

                novos = coletar_historico_dom(driver)

                if novos and (not historico or novos[0] != historico[0]):
                    added = False
                    with _history_lock:
                        for v in novos:
                            if v not in global_history:
                                global_history.insert(0, v)
                                added = True
                        if len(global_history) > 50:
                            global_history = global_history[:50]

                    if added:
                        with _history_lock:
                            lista = ", ".join(f"{v:.2f}x" for v in global_history[:20])
                            ultimo = global_history[0] if global_history else 0
                        send_telegram_text(
                            f"📊 **EBET AVIATOR - ÚLTIMOS 50**\n\n[{lista}]\n\nÚltimo: *{ultimo:.2f}x*",
                            throttle_seconds=10
                        )
                        if random.random() < 0.5:
                            screenshot_and_send(driver, "Histórico atualizado")

                    historico = novos[:]

                # HEARTBEAT: avisa que está vivo a cada ~30s
                if random.random() < 0.3:
                    send_telegram_text("✅ EBET Aviator ainda rodando (heartbeat)", throttle_seconds=30)

                time.sleep(15 + random.uniform(5, 10))   # ← EXATAMENTE o que você pediu

        except Exception as e:
            print("ERRO NO SCRAPER:", type(e).__name__, e)
            traceback.print_exc()
            send_telegram_text(f"🔥 ERRO: {type(e).__name__} → reiniciando em 15s")
            time.sleep(15)
            backoff = min(max_backoff, backoff * 2)

        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
            time.sleep(5)


# ====================== SUPERVISOR ======================
def supervisor_thread():
    restart_timestamps = []
    backoff = SUPERVISOR_BACKOFF_BASE

    while True:
        worker = threading.Thread(target=iniciar_scraper, name="scraper-worker", daemon=True)
        worker.start()
        send_telegram_text("🔁 Supervisor: worker iniciado")

        while worker.is_alive():
            worker.join(timeout=5)

        # Worker morreu
        ts = time.time()
        restart_timestamps.append(ts)
        restart_timestamps = [t for t in restart_timestamps if ts - t <= RESTART_WINDOW_SECONDS]

        send_telegram_text(f"⚠️ Worker finalizou. Restarts nos últimos {RESTART_WINDOW_SECONDS}s: {len(restart_timestamps)}")

        if len(restart_timestamps) >= RESTART_THRESHOLD:
            send_telegram_text("⚠️ Muitos restarts → reiniciando processo inteiro")
            python = sys.executable
            os.execv(python, [python] + sys.argv)

        time.sleep(backoff + random.uniform(0, 3))
        backoff = min(SUPERVISOR_BACKOFF_MAX, backoff * 2)


# ====================== FLASK ======================
@app.route("/api/history")
def api_history():
    with _history_lock:
        return jsonify(global_history)

@app.route("/api/last")
def api_last():
    with _history_lock:
        return jsonify(global_history[0] if global_history else None)

@app.route("/")
def home():
    return "EBET AVIATOR BOT - Supervisor + Histórico 50 + Polling 15-25s"


def _signal_handler(sig, frame):
    send_telegram_text("🛑 Processo finalizado por sinal")
    sys.exit(0)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


if __name__ == "__main__":
    sup = threading.Thread(target=supervisor_thread, name="supervisor", daemon=True)
    sup.start()

    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
