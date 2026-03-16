#!/usr/bin/env python3
# main.py - EBET Aviator (melhor tratamento de rate-limit, abre iframe.src em nova aba, cookies)
import os
import sys
import time
import json
import random
import threading
import traceback
import signal
from pathlib import Path

from flask import Flask, jsonify
import requests

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
from selenium.webdriver.support import expected_conditions as EC

# ---------------- CONFIG (hardcoded - ambiente de teste) ----------------
TELEGRAM_TOKEN = "8742776802:AAHSzD1qTwCqMEOdoW9_pT2l5GfmMBWUZQY"
TELEGRAM_CHAT_ID = "7427648935"

PHONE = "857789345"
PASSWORD = "max123ZICO"
URL = "https://ebet.co.mz/games/go/spribe?id=aviator"

# cookie file (persist session to reduce logins)
COOKIES_PATH = Path("/tmp/ebet_cookies.json")
SCREEN_DIR = Path("/tmp/ebet_aviator_steps")
SCREEN_DIR.mkdir(parents=True, exist_ok=True)

# supervisor params
SUPERVISOR_BACKOFF_BASE = 2
SUPERVISOR_BACKOFF_MAX = 300
RESTART_WINDOW_SECONDS = 300
RESTART_THRESHOLD = 8

# polling params
POLL_MIN = 8
POLL_MAX = 14
MAX_MISS_BEFORE_DEEP_CHECK = 3   # só checar page_source depois de N misses
RATE_LIMIT_BACKOFF_BASE = 8
RATE_LIMIT_BACKOFF_MAX = 600

app = Flask(__name__)

# shared state
_hlock = threading.Lock()
global_history = []

_last_telegram = 0
TG_THROTTLE = 6
PHOTO_THROTTLE = 30

# optional proxy from env var, e.g. PROXY="http://user:pass@ip:port"
PROXY = os.getenv("PROXY") or os.getenv("HTTP_PROXY") or None


# ---------------- Telegram helpers ----------------
def send_telegram_text(msg: str, throttle_seconds: float = TG_THROTTLE) -> bool:
    global _last_telegram
    now = time.time()
    if throttle_seconds and (now - _last_telegram) < throttle_seconds:
        return False
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=12,
        )
        _last_telegram = now
        return True
    except Exception as e:
        print("send_telegram_text failed:", e)
        return False


def send_telegram_photo(path: str, caption: str = "", throttle_seconds: float = PHOTO_THROTTLE) -> bool:
    global _last_telegram
    now = time.time()
    if throttle_seconds and (now - _last_telegram) < throttle_seconds:
        return False
    try:
        with open(path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": f},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                timeout=30,
            )
        _last_telegram = now
        return True
    except Exception as e:
        print("send_telegram_photo failed:", e)
        return False


def save_screenshot(driver, label: str) -> str:
    fname = f"{int(time.time())}_{abs(hash(label)) % 10000}.png"
    path = SCREEN_DIR / fname
    try:
        driver.save_screenshot(str(path))
    except Exception as e:
        print("save_screenshot fail:", e)
    return str(path)


# ---------------- DOM helpers ----------------
def safe_find_elements(driver, selector, max_retries=3, sleep_between=0.25):
    for _ in range(max_retries):
        try:
            return driver.find_elements(By.CSS_SELECTOR, selector)
        except StaleElementReferenceException:
            time.sleep(sleep_between)
            continue
        except Exception:
            time.sleep(sleep_between)
    return []


def click_aviator_if_found(driver):
    imgs = safe_find_elements(driver, "img.landing-page__item-image")
    for img in imgs:
        try:
            src = (img.get_attribute("src") or "").lower()
            alt = (img.get_attribute("alt") or "").lower()
            if "aviator" in src or "aviator" in alt:
                driver.execute_script("arguments[0].click();", img)
                return True
        except Exception:
            continue
    return False


def coletar_historico_from_frame(driver):
    out = []
    try:
        elems = safe_find_elements(driver, "div.payouts-wrapper .payout, div.payouts-block .payout, .payout")
        pat = r"(\d+(\.\d+)?)"
        for e in elems:
            try:
                txt = e.text.strip()
                m = __import__("re").search(pat, txt)
                if m:
                    out.append(float(m.group(1)))
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
    except Exception as e:
        print("coletar_historico_from_frame error:", e)
    return out


# conservative rate-limit check (only called rarely)
def conservative_page_shows_rate_limit(driver) -> bool:
    try:
        body = driver.page_source.lower()
        checks = ["rate limit", "too many requests", "429", "rate-limited", "try again later"]
        return any(token in body for token in checks)
    except Exception:
        return False


# ---------------- driver / cookies / proxy ----------------
def build_chrome_options():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # headers
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115 Safari/537.36")
    opts.add_argument("--lang=pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7")
    # proxy if present
    if PROXY:
        opts.add_argument(f'--proxy-server={PROXY}')
    return opts


def start_driver():
    opts = build_chrome_options()
    if os.path.exists("/usr/bin/chromium"):
        opts.binary_location = "/usr/bin/chromium"
    service = Service("/usr/bin/chromedriver") if os.path.exists("/usr/bin/chromedriver") else Service()
    driver = webdriver.Chrome(service=service, options=opts)
    # lightweight stealth injection
    try:
        stealth = r"""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        try{ Object.defineProperty(navigator, 'plugins', {get:()=>[1,2,3]}); }catch(e){}
        try{ Object.defineProperty(navigator, 'languages', {get:()=>['pt-BR','pt']}); }catch(e){}
        """
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth})
    except Exception:
        pass
    time.sleep(0.3)
    return driver


def save_cookies_to_file(driver):
    try:
        cookies = driver.get_cookies()
        COOKIES_PATH.write_text(json.dumps(cookies))
    except Exception as e:
        print("save_cookies_to_file failed:", e)


def load_cookies_from_file(driver):
    try:
        if not COOKIES_PATH.exists():
            return
        cookies = json.loads(COOKIES_PATH.read_text())
        for c in cookies:
            try:
                # remove domain attribute if incompatible
                if "sameSite" in c:
                    c.pop("sameSite", None)
                driver.add_cookie(c)
            except Exception:
                continue
    except Exception as e:
        print("load_cookies_from_file failed:", e)


# ---------------- open iframe.src in new tab ----------------
def open_iframe_src_in_new_tab(driver, iframe_el):
    try:
        src = iframe_el.get_attribute("src")
        if not src:
            return False
        driver.switch_to.default_content()
        driver.execute_script("window.open(arguments[0]);", src)
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(2 + random.random()*1.5)
        return True
    except Exception as e:
        print("open_iframe_src_in_new_tab failed:", e)
        return False


# ---------------- core scraper (improved rate-limit strategy) ----------------
def iniciar_scraper():
    global global_history
    base_rl_backoff = RATE_LIMIT_BACKOFF_BASE
    rl_backoff = base_rl_backoff

    while True:
        driver = None
        try:
            send_telegram_text("🟢 Iniciando EBET Aviator (tentar abrir iframe.src em nova aba)", throttle_seconds=6)

            driver = start_driver()
            wait = WebDriverWait(driver, 30)

            # If cookies available, try to reuse (reduces logins)
            driver.get("https://ebet.co.mz")
            try:
                load_cookies_from_file(driver)
            except Exception:
                pass

            # --- open target page and screenshot ---
            driver.get(URL)
            time.sleep(5 + random.random()*2)
            p = save_screenshot(driver, "Página inicial aberta")
            send_telegram_photo(p, caption="Página inicial aberta")

            # try click aviator on landing (if exists)
            click_aviator_if_found(driver)
            time.sleep(1.5 + random.random()*1.0)

            # TRY to load cookies BEFORE login (if cookies were invalid attempt will fail and we login)
            try:
                # if already logged in by cookies, skip login
                # detect presence of phone input to know if we need to login
                phone_present = False
                try:
                    driver.find_element(By.ID, "phone-input")
                    phone_present = True
                except Exception:
                    phone_present = False

                if phone_present:
                    # perform login (human-like)
                    try:
                        el_phone = driver.find_element(By.ID, "phone-input")
                        el_phone.clear()
                        el_phone.send_keys(PHONE)
                        time.sleep(0.6 + random.random()*0.4)
                        el_pwd = driver.find_element(By.ID, "password-input")
                        el_pwd.clear()
                        el_pwd.send_keys(PASSWORD)
                        time.sleep(0.5 + random.random()*0.4)
                        # submit button fallback selectors
                        try:
                            btn = driver.find_element(By.CSS_SELECTOR, "input.btn-session, button[type='submit']")
                            driver.execute_script("arguments[0].click();", btn)
                        except Exception:
                            try:
                                driver.execute_script("document.querySelector('input.btn-session')?.click();")
                            except Exception:
                                pass
                        time.sleep(3 + random.random()*1.5)
                        # try saving cookies after login
                        try:
                            save_cookies_to_file(driver)
                        except Exception:
                            pass
                        p = save_screenshot(driver, "Depois do login")
                        send_telegram_photo(p, caption="Depois do login")
                    except Exception as e:
                        print("Login attempt failed:", e)
                else:
                    # Already logged in (cookies worked)
                    send_telegram_text("✅ Sessão reutilizada via cookies (login pulado).", throttle_seconds=6)
            except Exception as e:
                print("Erro checando login:", e)

            # after login attempt, try to find the game's iframe
            iframe_el = None
            start_t = time.time()
            while time.time() - start_t < 45:
                try:
                    frames = driver.find_elements(By.TAG_NAME, "iframe")
                    for f in frames:
                        try:
                            src = (f.get_attribute("src") or "").lower()
                            title = (f.get_attribute("title") or "").lower()
                            if ("launch.spribegaming" in src) or ("aviator-next.spribegaming" in src) or ("spribegaming" in src) or ("game-iframe" in title):
                                iframe_el = f
                                break
                        except Exception:
                            continue
                    if iframe_el:
                        break
                except Exception:
                    pass
                time.sleep(1 + random.random()*0.5)

            if not iframe_el:
                send_telegram_text("❗ iframe do jogo não encontrado (timeout). Tentando reabrir página.", throttle_seconds=6)
                # quick retry by reloading the URL once
                try:
                    driver.get(URL)
                    time.sleep(4 + random.random()*1.5)
                except Exception:
                    pass
                # if still not found, raise and let supervisor/outer loop handle restart
                frames = driver.find_elements(By.TAG_NAME, "iframe")
                if not any("spribegaming" in (f.get_attribute("src") or "").lower() for f in frames):
                    raise RuntimeError("iframe do Aviator não encontrado")

            # Try to open iframe.src in new tab (preferred) to avoid wrapper page checks
            opened_new_tab = False
            try:
                if iframe_el:
                    if open_iframe_src_in_new_tab(driver, iframe_el):
                        opened_new_tab = True
                        send_telegram_text("🔗 Abriu iframe.src em nova aba (leremos DOM do jogo lá).", throttle_seconds=6)
                    else:
                        # try direct switch to iframe
                        try:
                            driver.switch_to.frame(iframe_el)
                        except Exception:
                            # final fallback: open src anyway by reading attribute and opening
                            src = iframe_el.get_attribute("src")
                            if src:
                                driver.execute_script("window.open(arguments[0]);", src)
                                driver.switch_to.window(driver.window_handles[-1])
                                opened_new_tab = True
            except Exception as e:
                print("Erro ao tentar abrir iframe.src:", e)

            time.sleep(2 + random.random()*1.2)
            p = save_screenshot(driver, "Dentro_do_contexto_do_jogo")
            send_telegram_photo(p, caption="Dentro do contexto do jogo")

            # initial collect
            historico_local = coletar_historico_from_frame(driver)
            with _hlock:
                global_history = historico_local[:50]

            # monitoring loop (adaptive)
            miss_count = 0
            rl_backoff = RATE_LIMIT_BACKOFF_BASE
            while True:
                # collect
                try:
                    novos = coletar_historico_from_frame(driver)
                except WebDriverException as e:
                    # treat as fatal for this run — let outer try restart
                    raise

                if novos:
                    # reset miss counter and rate-limit backoff
                    miss_count = 0
                    rl_backoff = RATE_LIMIT_BACKOFF_BASE
                    # detect change
                    with _hlock:
                        prev0 = global_history[0] if global_history else None
                    if not prev0 or (novos and novos[0] != prev0):
                        added = False
                        with _hlock:
                            for v in novos:
                                if v not in global_history:
                                    global_history.insert(0, v)
                                    added = True
                            if len(global_history) > 50:
                                global_history = global_history[:50]
                            snapshot = list(global_history)
                        if added:
                            lista = ", ".join(f"{x:.2f}x" for x in snapshot[:20])
                            send_telegram_text(f"📊 EBET AVIATOR — atualizados (top20):\n[{lista}]\nÚltimo: *{snapshot[0]:.2f}x*", throttle_seconds=10)
                            if random.random() < 0.5:
                                p = save_screenshot(driver, "Historico_atualizado")
                                send_telegram_photo(p, caption="Histórico atualizado")
                    historico_local = novos[:]
                else:
                    # nothing found => increase miss_count, only do a heavy page_source check after several misses
                    miss_count += 1
                    if miss_count >= MAX_MISS_BEFORE_DEEP_CHECK:
                        # conservative page source check
                        if conservative_page_shows_rate_limit(driver):
                            # rate-limit detected — backoff with jitter and try to open iframe.src again
                            sleep_for = min(RATE_LIMIT_BACKOFF_MAX, rl_backoff) + random.uniform(0.5, 2.0)
                            send_telegram_text(f"⚠️ Rate limit detectado — dormindo {int(sleep_for)}s", throttle_seconds=6)
                            time.sleep(sleep_for)
                            rl_backoff = min(RATE_LIMIT_BACKOFF_MAX, rl_backoff * 2)
                            # attempt to recover: try to open iframe.src in new tab again (if not already)
                            try:
                                frames = driver.find_elements(By.TAG_NAME, "iframe")
                                iframe_candidate = None
                                for f in frames:
                                    src = (f.get_attribute("src") or "").lower()
                                    if "spribegaming" in src or "aviator" in src:
                                        iframe_candidate = f
                                        break
                                if iframe_candidate:
                                    open_iframe_src_in_new_tab(driver, iframe_candidate)
                                    time.sleep(2 + random.random()*1.0)
                            except Exception:
                                pass
                        else:
                            # no explicit rate-limit token found — just wait more (avoid aggressive checks)
                            time.sleep(6 + random.random()*4)
                    else:
                        # small wait before next try
                        time.sleep(1.5 + random.random()*1.5)

                # adaptive sleep
                sleep_period = random.uniform(POLL_MIN, POLL_MAX)
                # if we've seen recent misses, stretch the sleep
                if miss_count >= 2:
                    sleep_period = min(60, sleep_period + miss_count * 4)
                time.sleep(sleep_period)

        except Exception as e:
            print("Erro no scraper:", type(e).__name__, e)
            traceback.print_exc()
            try:
                send_telegram_text(f"🔥 ERRO SCRAPER: {type(e).__name__} - {e}", throttle_seconds=6)
            except Exception:
                pass
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass
            # escalate backoff before restart to avoid hot loops
            time.sleep(min(RATE_LIMIT_BACKOFF_MAX, rl_backoff) + random.uniform(1, 4))
            rl_backoff = min(RATE_LIMIT_BACKOFF_MAX, rl_backoff * 2)
            continue
        finally:
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass
            time.sleep(1)


# ---------------- Supervisor ----------------
def supervisor_thread():
    restart_timestamps = []
    backoff = SUPERVISOR_BACKOFF_BASE
    while True:
        worker = threading.Thread(target=iniciar_scraper, name="scraper-worker", daemon=True)
        worker.start()
        send_telegram_text("🔁 Supervisor: worker iniciado", throttle_seconds=6)
        while worker.is_alive():
            worker.join(timeout=5)
        ts = time.time()
        restart_timestamps.append(ts)
        restart_timestamps = [t for t in restart_timestamps if ts - t <= RESTART_WINDOW_SECONDS]
        send_telegram_text(f"⚠️ Supervisor: worker finalizou. Restarts recentes: {len(restart_timestamps)}", throttle_seconds=6)
        print("[supervisor] worker died; restarts_in_window=", len(restart_timestamps))
        if len(restart_timestamps) >= RESTART_THRESHOLD:
            send_telegram_text("⚠️ Muitos restarts em curto período → execv restart", throttle_seconds=6)
            try:
                python = sys.executable
                os.execv(python, [python] + sys.argv)
            except Exception as ex:
                print("execv failed:", ex)
                time.sleep(backoff)
                backoff = min(SUPERVISOR_BACKOFF_MAX, backoff * 2)
                continue
        time.sleep(backoff + random.random()*2)
        backoff = min(SUPERVISOR_BACKOFF_MAX, backoff * 2)


# ---------------- Flask endpoints ----------------
@app.route("/api/history")
def api_history():
    with _hlock:
        return jsonify(global_history)


@app.route("/api/last")
def api_last():
    with _hlock:
        return jsonify(global_history[0] if global_history else None)


@app.route("/")
def index():
    return "EBET AVIATOR - histórico acumulado (até 50)."

# ---------------- graceful shutdown ----------------
def _signal_handler(sig, frame):
    try:
        send_telegram_text(f"🛑 Processo recebendo sinal {sig} — desligando.", throttle_seconds=0)
    except Exception:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ---------------- entrypoint ----------------
if __name__ == "__main__":
    sup = threading.Thread(target=supervisor_thread, name="supervisor", daemon=True)
    sup.start()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
