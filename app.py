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
if "messages" not in st.session_state: st.session_state.messages = []
if "chat_started" not in st.session_state: st.session_state.chat_started = False
if "current_start_pz" not in st.session_state: st.session_state.current_start_pz = None

# --- PANEL BOCZNY ---
with st.sidebar:
    st.title("⚙️ Panel Sterowania")
    
    st.radio("Model AI:", list(MODEL_MAP.keys()), key="selected_model_label")
    active_model_id = MODEL_MAP[st.session_state.selected_model_label]
    
    st.caption(f"🧠 Model: `{active_model_id}`")
    st.caption(f"🌡️ Temp: `{TEMPERATURE}`")
    
    # Ręczna zmiana klucza
    manual_key = st.checkbox("Ręczny wybór klucza")
    if manual_key:
        key_options = [f"Klucz {i+1} (...{API_KEYS[i][-4:]})" for i in range(len(API_KEYS))]
        selected_key_label = st.selectbox("Wybierz aktywny klucz:", key_options, index=st.session_state.key_index)
        st.session_state.key_index = key_options.index(selected_key_label)
    else:
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
    if st.button("🚀 Nowa sprawa / Reset", type="primary"):
        if not st.session_state.operator or not st.session_state.grupa:
            st.error("Wybierz Operatora i Grupę!")
        else:
            cookies['operator'] = st.session_state.operator
            cookies['grupa'] = st.session_state.grupa
            cookies['selected_model_label'] = st.session_state.selected_model_label
            cookies.save()
            st.session_state.messages = []
            st.session_state.chat_started = True
            st.session_state.current_start_pz = None
            # Losujemy klucz na nową sprawę
            if not manual_key:
                st.session_state.key_index = random.randint(0, len(API_KEYS) - 1)
            # Czyścimy cache w sesji
            for k in list(st.session_state.keys()):
                if k.startswith("cache_"): del st.session_state[k]
            st.rerun()

    if st.button("🗑️ Reset Sesji (Wyloguj)"):
        st.session_state.clear()
        cookies.clear()
        cookies.save()
        st.rerun()

# ==========================================
# 🖥️ GŁÓWNY INTERFEJS
# ==========================================
st.title(f"🤖 Szturchacz")

if not st.session_state.chat_started:
    st.info("👈 Skonfiguruj panel i kliknij 'Nowa sprawa / Reset'.")
else:
    SYSTEM_INSTRUCTION_BASE = st.secrets["SYSTEM_PROMPT"]
    parametry_startowe = f"\ndomyslny_operator={st.session_state.operator}\ndomyslna_data={datetime.now().strftime('%d.%m')}\nGrupa_Operatorska={st.session_state.grupa}\ndomyslny_tryb={wybrany_tryb_kod}"
    FULL_PROMPT = SYSTEM_INSTRUCTION_BASE + parametry_startowe

    def get_or_create_model(model_name, full_prompt):
        prompt_hash = hashlib.md5(full_prompt.encode()).hexdigest()
        cache_key = f"cache_{st.session_state.key_index}_{model_name}_{prompt_hash}"
        
        if st.session_state.get(cache_key):
            try: return genai.GenerativeModel.from_cached_content(st.session_state[cache_key])
            except: del st.session_state[cache_key]
        
        genai.configure(api_key=get_current_key())
        # Cache tylko dla 1.5 Pro
        if "gemini-1.5-pro" in model_name:
            with st.spinner(f"Tworzenie cache dla klucza {st.session_state.key_index + 1}..."):
                cache = caching.CachedContent.create(model=f'models/{model_name}', system_instruction=full_prompt, ttl=timedelta(hours=1))
                st.session_state[cache_key] = cache
                return genai.GenerativeModel.from_cached_content(cache)
        else:
            return genai.GenerativeModel(model_name=model_name, system_instruction=full_prompt)

    def call_gemini_with_rotation(history, user_input):
        max_retries = len(API_KEYS)
        attempts = 0
        while attempts < max_retries:
            try:
                genai.configure(api_key=get_current_key())
                model = get_or_create_model(active_model_id, FULL_PROMPT)
                chat = model.start_chat(history=history)
                response = chat.send_message(user_input, generation_config={"temperature": TEMPERATURE})
                return response.text, True
            except Exception as e:
                if isinstance(e, google_exceptions.ResourceExhausted) or "429" in str(e) or "Quota" in str(e) or "403" in str(e):
                    attempts += 1
                    if not manual_key:
                        rotate_key()
                        st.toast(f"🔄 Rotacja: Klucz {st.session_state.key_index + 1}")
                        time.sleep(1)
                    else:
                        return f"❌ Limit klucza {st.session_state.key_index + 1} wyczerpany. Zmień klucz ręcznie.", False
                else:
                    return f"Błąd API: {str(e)}", False
        return "❌ Wszystkie klucze wyczerpane.", False

    # --- LOGIKA EKRANU ---
    if len(st.session_state.messages) == 0:
        # KROK 1: Pierwszy wsad (Wsad-First)
        st.subheader(f"📥 Pierwszy wsad ({st.session_state.operator})")
        wsad_input = st.text_area("Wklej tabelkę i kopertę sprawy:", height=350, placeholder="Wklej dane tutaj...")
        if st.button("🚀 Rozpocznij analizę", type="primary"):
            if wsad_input:
                input_pz = parse_pz(wsad_input)
                st.session_state.current_start_pz = input_pz if input_pz else "PZ_START"
                st.session_state.messages.append({"role": "user", "content": wsad_input})
                
                with st.spinner("Analiza..."):
                    res_text, success = call_gemini_with_rotation([], wsad_input)
                    if success:
                        st.session_state.messages.append({"role": "model", "content": res_text})
                        st.rerun()
                    else: st.error(res_text)
            else: st.error("Wsad nie może być pusty!")
    else:
        # KROK 2: Tryb Czatu (dla sesji wielokrokowych)
        st.subheader(f"💬 Rozmowa: {st.session_state.operator}")
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        
        if prompt := st.chat_input("Odpowiedz AI (np. SESJA WYNIK)..."):
            with st.chat_message("user"): st.markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})
            
            with st.chat_message("model"):
                with st.spinner("Analizuję..."):
                    history_api = [{"role": m["role"], "parts": [m["content"]]} for m in st.session_state.messages[:-1]]
                    res_text, success = call_gemini_with_rotation(history_api, prompt)
                    if success:
                        st.markdown(res_text)
                        st.session_state.messages.append({"role": "model", "content": res_text})
                        # Logowanie statystyk przy finale
                        if 'cop#' in res_text.lower() and 'c#' in res_text.lower():
                            end_pz = parse_pz(res_text)
                            log_session_and_transition(st.session_state.operator, st.session_state.current_start_pz, end_pz if end_pz else "PZ_END")
                    else: st.error(res_text)
