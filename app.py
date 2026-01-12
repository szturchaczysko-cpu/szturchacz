import streamlit as st
import google.generativeai as genai
from google.generativeai import caching
from google.api_core import exceptions as google_exceptions
from datetime import datetime, timedelta
import locale, time, json, re, pytz, hashlib, random
import firebase_admin
from firebase_admin import credentials, firestore
from streamlit_cookies_manager import EncryptedCookieManager

# --- 0. KONFIGURACJA ---
st.set_page_config(page_title="Szturchacz AI", layout="wide")
try: locale.setlocale(locale.LC_TIME, "pl_PL.UTF-8")
except: pass

if not firebase_admin._apps:
    creds_dict = json.loads(st.secrets["FIREBASE_CREDS"])
    creds = credentials.Certificate(creds_dict)
    firebase_admin.initialize_app(creds)
db = firestore.client()

cookies = EncryptedCookieManager(password=st.secrets.get("COOKIE_PASSWORD", "dev_pass"))
if not cookies.ready(): st.stop()

# ==========================================
# 🔒 LOGOWANIE I ROUTING
# ==========================================
if "operator" not in st.session_state: st.session_state.operator = cookies.get("op_name", "")
if "password_correct" not in st.session_state: st.session_state.password_correct = cookies.get("auth", "") == "ok"

if not st.session_state.password_correct:
    st.header("🔒 Zaloguj się")
    input_pwd = st.text_input("Wpisz swoje hasło:", type="password")
    if st.button("Wejdź"):
        query = db.collection("operator_configs").where("password", "==", input_pwd).limit(1).get()
        if query:
            op_doc = query[0]
            st.session_state.operator = op_doc.id
            st.session_state.password_correct = True
            cookies["op_name"] = op_doc.id
            cookies["auth"] = "ok"
            cookies.save()
            st.rerun()
        else: st.error("Błędne hasło!")
    st.stop()

# --- SPRAWDZENIE ROUTINGU (Jaki plik uruchomić?) ---
op_name = st.session_state.operator
cfg = db.collection("operator_configs").document(op_name).get().to_dict() or {}
target_file = cfg.get("app_file", "app.py")

# Jeśli admin przypisał inny plik niż ten (app.py), wczytujemy go i kończymy ten skrypt
if target_file != "app.py":
    try:
        with open(target_file, encoding="utf-8") as f:
            code = f.read()
            exec(code, globals())
            st.stop() # Zatrzymuje dalsze wykonywanie app.py
    except FileNotFoundError:
        st.error(f"Błąd: Nie znaleziono pliku {target_file} w repozytorium!")
        st.stop()

# ==========================================
# 🚀 JEŚLI TARGET_FILE == "app.py", KONTYNUUJEMY TUTAJ
# ==========================================

# Pobieranie ustawień globalnych (Diamenty)
global_cfg = db.collection("admin_config").document("global_settings").get().to_dict() or {}
show_diamonds_globally = global_cfg.get("show_diamonds", True)

# (Reszta Twojego kodu Szturchacza - statystyki, sidebar, czat...)
# Poniżej fragment sidebaru z warunkiem na diamenty:

def parse_pz(text):
    if not text: return None
    match = re.search(r'COP#\s*PZ\s*:\s*(PZ\d+)', text, re.IGNORECASE)
    if match: return match.group(1).upper()
    return None

def log_stats(op_name, start_pz, end_pz, key_idx):
    tz_pl = pytz.timezone('Europe/Warsaw')
    today = datetime.now(tz_pl).strftime("%Y-%m-%d")
    doc_ref = db.collection("stats").document(today).collection("operators").document(op_name)
    upd = {"sessions_completed": firestore.Increment(1)}
    if start_pz and end_pz:
        upd[f"pz_transitions.{start_pz}_to_{end_pz}"] = firestore.Increment(1)
        if end_pz == "PZ6":
            db.collection("global_stats").document("totals").collection("operators").document(op_name).set({"total_diamonds": firestore.Increment(1)}, merge=True)
    doc_ref.set(upd, merge=True)
    db.collection("key_usage").document(today).set({str(key_idx + 1): firestore.Increment(1)}, merge=True)

# Pobieranie danych do sidebaru
tz_pl = pytz.timezone('Europe/Warsaw')
today_s = datetime.now(tz_pl).strftime("%Y-%m-%d")
today_data = db.collection("stats").document(today_s).collection("operators").document(op_name).get().to_dict() or {}
today_diamonds = sum(v for k, v in today_data.get("pz_transitions", {}).items() if k.endswith("_to_PZ6"))
global_data = db.collection("global_stats").document("totals").collection("operators").document(op_name).get().to_dict() or {}
all_time_diamonds = global_data.get("total_diamonds", 0)

with st.sidebar:
    st.title(f"👤 {op_name}")
    
    # --- WARUNKOWE WYŚWIETLANIE DIAMENTÓW ---
    if show_diamonds_globally:
        st.markdown(f"### 💎 Zamówieni kurierzy\n**Dziś:** {today_diamonds} | **Łącznie:** {all_time_diamonds}")
    else:
        st.caption("📊 Statystyki diamentów są obecnie ukryte przez Admina.")
    
    st.markdown("---")
    # (Reszta sidebaru: wiadomości, model, klucze, reset...)
    admin_msg = cfg.get("admin_message", "")
    msg_read = cfg.get("message_read", False)
    if admin_msg:
        if not msg_read:
            st.error(f"📢 **WIADOMOŚĆ:**\n\n{admin_msg}")
            if st.button("✅ Odczytałem"):
                db.collection("operator_configs").document(op_name).update({"message_read": True})
                st.rerun()
        else:
            with st.expander("📩 Wiadomość od Admina"): st.write(admin_msg)

    st.markdown("---")
    model_label = st.radio("Model:", ["Gemini 1.5 Pro (2.5)", "Gemini 3.0 Pro"], index=0 if cookies.get("mod") != "3.0" else 1)
    cookies["mod"] = "1.5" if "1.5" in model_label else "3.0"
    cookies.save()
    active_model = "gemini-1.5-pro" if "1.5" in model_label else "gemini-3-pro-preview"
    
    fixed_key_idx = cfg.get("assigned_key_index", 0)
    if fixed_key_idx > 0:
        st.session_state.key_index = fixed_key_idx - 1
        st.success(f"🔑 Klucz stały: {fixed_key_idx}")
    else:
        if "key_index" not in st.session_state: st.session_state.key_index = random.randint(0, 4)
        st.caption(f"🔑 Klucz (Rotator): {st.session_state.key_index + 1}")

    if st.button("🗑️ Nowa sprawa / Reset"):
        st.session_state.messages = []
        st.session_state.chat_started = False
        st.rerun()
    
    if st.button("🚪 Wyloguj"):
        st.session_state.clear()
        cookies.clear()
        cookies.save()
        st.rerun()

# --- CZAT (Logika bez zmian) ---
# (Tutaj wklej resztę kodu czatu z poprzedniej wersji, zaczynając od st.title("🤖 Szturchacz AI"))
# ...
