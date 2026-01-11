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

# --- BAZA DANYCH ---
if not firebase_admin._apps:
    creds_dict = json.loads(st.secrets["FIREBASE_CREDS"])
    creds = credentials.Certificate(creds_dict)
    firebase_admin.initialize_app(creds)
db = firestore.client()

# --- CIASTECZKA ---
cookies = EncryptedCookieManager(password=st.secrets.get("COOKIE_PASSWORD", "dev_pass"))
if not cookies.ready(): st.stop()

# --- FUNKCJE STATYSTYK ---
def parse_pz(text):
    if not text: return None
    match = re.search(r'COP#\s*PZ\s*:\s*(PZ\d+)', text, re.IGNORECASE)
    if match: return match.group(1).upper()
    return None

def get_pz_value(pz_string):
    if pz_string == "PZ_START": return -1
    if pz_string == "PZ_END": return 999
    if pz_string and pz_string.startswith("PZ"):
        try: return int(pz_string[2:])
        except: return None
    return None

def log_stats(op_name, start_pz, end_pz, key_idx):
    tz_pl = pytz.timezone('Europe/Warsaw')
    today = datetime.now(tz_pl).strftime("%Y-%m-%d")
    # 1. Sesje i Przejścia
    doc_ref = db.collection("stats").document(today).collection("operators").document(op_name)
    upd = {"sessions_completed": firestore.Increment(1)}
    s_v, e_v = get_pz_value(start_pz), get_pz_value(end_pz)
    if s_v is not None and e_v is not None and e_v > s_v:
        upd[f"pz_transitions.{start_pz}_to_{end_pz}"] = firestore.Increment(1)
        # Jeśli diament (PZ6) -> inkrementuj licznik globalny
        if end_pz == "PZ6":
            db.collection("global_stats").document("totals").collection("operators").document(op_name).set({
                "total_diamonds": firestore.Increment(1)
            }, merge=True)
    doc_ref.set(upd, merge=True)
    # 2. Zużycie klucza
    db.collection("key_usage").document(today).set({str(key_idx + 1): firestore.Increment(1)}, merge=True)

# ==========================================
# 🔒 LOGOWANIE PRZEZ UNIKALNE HASŁO
# ==========================================
if "operator" not in st.session_state: st.session_state.operator = cookies.get("op_name", "")
if "password_correct" not in st.session_state: st.session_state.password_correct = cookies.get("auth", "") == "ok"

if not st.session_state.password_correct:
    st.header("🔒 Zaloguj się do Szturchacza")
    input_pwd = st.text_input("Wpisz swoje hasło:", type="password")
    if st.button("Wejdź"):
        # Szukamy operatora po haśle w bazie
        query = db.collection("operator_configs").where("password", "==", input_pwd).limit(1).get()
        if query:
            op_doc = query[0]
            st.session_state.operator = op_doc.id
            st.session_state.password_correct = True
            cookies["op_name"] = op_doc.id
            cookies["auth"] = "ok"
            cookies.save()
            st.rerun()
        else:
            st.error("Błędne hasło!")
    st.stop()

# ==========================================
# 🔑 POBIERANIE CONFIGU I DIAMENTÓW
# ==========================================
op_name = st.session_state.operator
cfg_ref = db.collection("operator_configs").document(op_name)
cfg = cfg_ref.get().to_dict() or {}

# Pobieranie diamentów (Dziś)
tz_pl = pytz.timezone('Europe/Warsaw')
today_s = datetime.now(tz_pl).strftime("%Y-%m-%d")
today_data = db.collection("stats").document(today_s).collection("operators").document(op_name).get().to_dict() or {}
today_diamonds = sum(v for k, v in today_data.get("pz_transitions", {}).items() if k.endswith("_to_PZ6"))

# Pobieranie diamentów (All Time)
global_data = db.collection("global_stats").document("totals").collection("operators").document(op_name).get().to_dict() or {}
all_time_diamonds = global_data.get("total_diamonds", 0)

# ==========================================
# 🚀 SIDEBAR
# ==========================================
with st.sidebar:
    st.title(f"👤 {op_name}")
    
    # --- DIAMENTY ---
    st.markdown(f"### 💎 Zamówieni kurierzy")
    c1, c2 = st.columns(2)
    c1.metric("Dzisiaj", today_diamonds)
    c2.metric("Łącznie", all_time_diamonds)
    st.markdown("---")

    # --- WIADOMOŚĆ OD ADMINA ---
    admin_msg = cfg.get("admin_message", "")
    msg_read = cfg.get("message_read", False)
    if admin_msg:
        if not msg_read:
            st.error(f"📢 **WIADOMOŚĆ:**\n\n{admin_msg}")
            if st.button("✅ Odczytałem"):
                cfg_ref.update({"message_read": True})
                st.rerun()
        else:
            with st.expander("📩 Poprzednia wiadomość"):
                st.write(admin_msg)

    st.markdown("---")
    # Model i Klucz
    model_label = st.radio("Model:", ["Gemini 1.5 Pro (2.5)", "Gemini 3.0 Pro"], 
                           index=0 if cookies.get("mod") != "3.0" else 1)
    cookies["mod"] = "1.5" if "1.5" in model_label else "3.0"
    cookies.save()
    
    active_model = "gemini-1.5-pro" if "1.5" in model_label else "gemini-3-pro-preview"
    
    # Klucz stały vs rotator
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

# ==========================================
# 🖥️ CZAT
# ==========================================
st.title("🤖 Szturchacz AI")

if "chat_started" not in st.session_state: st.session_state.chat_started = False

if not st.session_state.chat_started:
    st.info(f"Witaj {op_name}! Wklej wsad poniżej, aby zacząć.")
    wsad = st.text_area("Wklej tabelkę i kopertę:", height=300)
    if st.button("🚀 Analizuj", type="primary"):
        if wsad:
            st.session_state.current_start_pz = parse_pz(wsad) or "PZ_START"
            st.session_state.messages = [{"role": "user", "content": wsad}]
            st.session_state.chat_started = True
            
            # Wywołanie API
            SYSTEM_PROMPT = st.secrets["SYSTEM_PROMPT"]
            FULL_PROMPT = SYSTEM_PROMPT + f"\ndomyslny_operator={op_name}\nGrupa_Operatorska={cfg.get('role', 'Operatorzy_DE')}"
            
            with st.spinner("Analiza..."):
                API_KEYS = st.secrets["API_KEYS"]
                genai.configure(api_key=API_KEYS[st.session_state.key_index])
                model = genai.GenerativeModel(active_model, system_instruction=FULL_PROMPT)
                try:
                    resp = model.generate_content(wsad, generation_config={"temperature": 0.0})
                    st.session_state.messages.append({"role": "model", "content": resp.text})
                    log_stats(op_name, st.session_state.current_start_pz, parse_pz(resp.text) or "PZ_END", st.session_state.key_index)
                    st.rerun()
                except Exception as e:
                    st.error(f"Błąd: {e}")
else:
    for m in st.session_state.messages:
        with st.chat_message(m["role"]): st.markdown(m["content"])
    
    if prompt := st.chat_input("Odpowiedz..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.rerun()
