import streamlit as st
import google.generativeai as genai
from google.generativeai import caching
from google.api_core import exceptions as google_exceptions
from datetime import datetime, timedelta
import locale
import time
import json
import re
import pytz
import hashlib
import random
import firebase_admin
from firebase_admin import credentials, firestore
from streamlit_cookies_manager import EncryptedCookieManager

# --- 0. KONFIGURACJA ŚRODOWISKA ---
st.set_page_config(page_title="Szturchacz AI - Ultra", layout="wide")
try:
    locale.setlocale(locale.LC_TIME, "pl_PL.UTF-8")
except: pass

# --- MENEDŻER CIASTECZEK ---
cookies = EncryptedCookieManager(
    password=st.secrets.get("COOKIE_PASSWORD", "default_password_for_local_dev")
)
if not cookies.ready():
    st.stop()

# --- INICJALIZACJA BAZY DANYCH ---
try:
    if not firebase_admin._apps:
        creds_dict = json.loads(st.secrets["FIREBASE_CREDS"])
        creds = credentials.Certificate(creds_dict)
        firebase_admin.initialize_app(creds)
    db = firestore.client()
except Exception as e:
    st.error(f"Błąd połączenia z bazą danych: {e}")
    st.stop()

# --- FUNKCJE POMOCNICZE ---
def parse_pz(text):
    if not text: return None
    match = re.search(r'PZ\s*[:]*\s*(\d+)', text, re.IGNORECASE)
    if match: return f"PZ{match.group(1)}"
    return None

def get_pz_value(pz_string):
    if pz_string == "PZ_START": return -1
    if pz_string == "PZ_END": return 999
    if pz_string and pz_string.startswith("PZ"):
        try: return int(pz_string[2:])
        except: return None
    return None

def log_session_and_transition(operator_name, start_pz, end_pz):
    tz_pl = pytz.timezone('Europe/Warsaw')
    today_str = datetime.now(tz_pl).strftime("%Y-%m-%d")
    try:
        doc_ref = db.collection("stats").document(today_str).collection("operators").document(operator_name)
        update_data = {"sessions_completed": firestore.Increment(1)}
        start_val = get_pz_value(start_pz)
        end_val = get_pz_value(end_pz)
        if start_val is not None and end_val is not None and end_val > start_val:
             transition_key = f"pz_transitions.{start_pz}_to_{end_pz}"
             update_data[transition_key] = firestore.Increment(1)
        doc_ref.set(update_data, merge=True)
    except: pass

# ==========================================
# 🔒 BRAMKA BEZPIECZEŃSTWA
# ==========================================
def check_password():
    if st.session_state.get("password_correct"): return True
    if cookies.get("password_correct") == "true":
        st.session_state.password_correct = True
        return True
    st.header("🔒 Dostęp chroniony")
    pwd = st.text_input("Hasło:", type="password", key="password_input")
    if st.button("Zaloguj"):
        if st.session_state.password_input == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            cookies['password_correct'] = 'true'
            cookies.save()
            st.rerun()
        else: st.error("😕 Błędne hasło")
    return False

if not check_password(): st.stop()

# ==========================================
# 🔑 MENEDŻER KLUCZY
# ==========================================
API_KEYS = st.secrets["API_KEYS"]
if "key_index" not in st.session_state:
    st.session_state.key_index = random.randint(0, len(API_KEYS) - 1)

def get_current_key(): return API_KEYS[st.session_state.key_index]
def rotate_key():
    st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)
    return st.session_state.key_index

# ==========================================
# 🚀 KONFIGURACJA MODELI
# ==========================================
MODEL_MAP = {
    "Gemini 1.5 Pro (2.5) - Zalecany": "gemini-2.5-pro",
    "Gemini 3.0 Pro - Chirurgiczny": "gemini-3-pro-preview"
}
TEMPERATURE = 0.0

# Inicjalizacja stanu
if "operator" not in st.session_state: st.session_state.operator = cookies.get("operator", "")
if "grupa" not in st.session_state: st.session_state.grupa = cookies.get("grupa", "")
if "selected_model_label" not in st.session_state: st.session_state.selected_model_label = cookies.get("selected_model_label", "Gemini 1.5 Pro (2.5) - Zalecany")
if "analysis_done" not in st.session_state: st.session_state.analysis_done = False

# --- PANEL BOCZNY ---
with st.sidebar:
    st.title("⚙️ Panel Sterowania")
    
    st.radio("Model AI:", list(MODEL_MAP.keys()), key="selected_model_label")
    active_model_id = MODEL_MAP[st.session_state.selected_model_label]
    
    st.caption(f"🧠 Model: `{active_model_id}`")
    st.caption(f"🌡️ Temp: `{TEMPERATURE}`")
    st.caption(f"🔑 Klucz: {st.session_state.key_index + 1}/{len(API_KEYS)}")
    st.markdown("---")
    
    st.selectbox("Operator:", ["", "Emilia", "Oliwia", "Iwona", "Marlena", "Magda", "Sylwia", "Ewelina", "Klaudia", "Marta"], key="operator")
    st.selectbox("Grupa:", ["", "Operatorzy_DE", "Operatorzy_FR", "Operatorzy_UK/PL"], key="grupa")
    
    TRYBY_DICT = {
        "Standard (Panel + Koperta)": "od_szturchacza",
        "WhatsApp (Rolka + Panel)": "WA",
        "E-mail (Rolka + Panel)": "MAIL",
        "Forum/Inne (Wpis + Panel)": "FORUM"
    }
    st.selectbox("Tryb Startowy:", list(TRYBY_DICT.keys()), key="tryb_label")
    wybrany_tryb_kod = TRYBY_DICT[st.session_state.tryb_label]
    
    st.markdown("---")
    if st.button("🗑️ Nowa sprawa / Reset"):
        st.session_state.analysis_done = False
        st.session_state.key_index = random.randint(0, len(API_KEYS) - 1)
        st.rerun()

    if st.button("🗑️ Reset Sesji (Wyloguj)"):
        st.session_state.clear()
        cookies.clear()
        cookies.save()
        st.rerun()

# ==========================================
# 🖥️ GŁÓWNY INTERFEJS
# ==========================================
st.title("🤖 Szturchacz AI")

if not st.session_state.analysis_done:
    st.subheader("📥 Przygotowanie wsadu")
    if not st.session_state.operator or not st.session_state.grupa:
        st.warning("👈 Najpierw wybierz Operatora i Grupę w panelu bocznym.")
    else:
        wsad_input = st.text_area("Wklej tutaj tabelkę i kopertę sprawy:", height=400, placeholder="Wklej dane tutaj...")
        
        if st.button("🚀 Analizuj sprawę", type="primary"):
            if not wsad_input:
                st.error("Wsad jest pusty!")
            else:
                # Zapisz ustawienia w ciasteczkach
                cookies['operator'] = st.session_state.operator
                cookies['grupa'] = st.session_state.grupa
                cookies['selected_model_label'] = st.session_state.selected_model_label
                cookies.save()

                with st.spinner("Analiza w toku (Load Balancing + Cache)..."):
                    SYSTEM_PROMPT = st.secrets["SYSTEM_PROMPT"]
                    now = datetime.now().strftime("%d.%m")
                    parametry = f"\ndomyslny_operator={st.session_state.operator}\ndomyslna_data={now}\nGrupa_Operatorska={st.session_state.grupa}\ndomyslny_tryb={wybrany_tryb_kod}"
                    FULL_PROMPT = SYSTEM_PROMPT + parametry
                    
                    max_retries = len(API_KEYS)
                    attempts = 0
                    success = False
                    
                    start_pz = parse_pz(wsad_input)
                    if not start_pz: start_pz = "PZ_START"

                    # Losujemy klucz startowy dla tego zapytania
                    st.session_state.key_index = random.randint(0, len(API_KEYS) - 1)

                    while attempts < max_retries and not success:
                        try:
                            genai.configure(api_key=get_current_key())
                            
                            # --- LOGIKA CACHE (Tylko dla 1.5 Pro) ---
                            if "gemini-1.5-pro" in active_model_id:
                                prompt_hash = hashlib.md5(FULL_PROMPT.encode()).hexdigest()
                                cache_key = f"cache_{st.session_state.key_index}_{active_model_id}_{prompt_hash}"
                                
                                if cache_key not in st.session_state:
                                    cache = caching.CachedContent.create(
                                        model=f'models/{active_model_id}',
                                        system_instruction=FULL_PROMPT,
                                        ttl=timedelta(hours=1)
                                    )
                                    st.session_state[cache_key] = cache
                                
                                model = genai.GenerativeModel.from_cached_content(st.session_state[cache_key])
                            else:
                                # Dla 3.0 Pro bez cache
                                model = genai.GenerativeModel(model_name=active_model_id, system_instruction=FULL_PROMPT)
                            
                            # Wywołanie (Jeden strzał!)
                            response = model.generate_content(wsad_input, generation_config={"temperature": TEMPERATURE})
                            st.session_state.last_result = response.text
                            st.session_state.analysis_done = True
                            success = True
                            
                            # Statystyki
                            if 'cop#' in response.text.lower() and 'c#' in response.text.lower():
                                end_pz = parse_pz(response.text)
                                log_session_and_transition(st.session_state.operator, start_pz, end_pz if end_pz else "PZ_END")
                            
                        except Exception as e:
                            if isinstance(e, google_exceptions.ResourceExhausted) or "429" in str(e) or "Quota" in str(e) or "403" in str(e):
                                attempts += 1
                                rotate_key()
                                time.sleep(1)
                            else:
                                st.error(f"Błąd API: {e}")
                                break
                    
                    if success: st.rerun()
                    else: st.error("Wszystkie klucze zajęte. Spróbuj za chwilę.")

else:
    st.subheader(f"✅ Wynik analizy ({st.session_state.operator})")
    st.markdown(st.session_state.last_result)
    st.markdown("---")
    if st.button("⬅️ Analizuj kolejną sprawę"):
        st.session_state.analysis_done = False
        st.session_state.key_index = random.randint(0, len(API_KEYS) - 1)
        st.rerun()
