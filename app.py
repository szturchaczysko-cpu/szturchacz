import streamlit as st
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from datetime import datetime
import locale
import time
import json
import re
import pytz
import firebase_admin
from firebase_admin import credentials, firestore

# --- 0. KONFIGURACJA ŚRODOWISKA ---
st.set_page_config(page_title="Szturchacz AI - Gemini 3.0 Pro", layout="wide")
try:
    locale.setlocale(locale.LC_TIME, "pl_PL.UTF-8")
except: pass

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

# --- FUNKCJE POMOCNICZE (STATYSTYKI) ---
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
    st.header("🔒 Dostęp chroniony")
    pwd = st.text_input("Hasło:", type="password")
    if st.button("Zaloguj"):
        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            st.rerun()
        else: st.error("😕 Błędne hasło")
    return False

if not check_password(): st.stop()

# ==========================================
# 🔑 MENEDŻER KLUCZY (ROTATOR)
# ==========================================
try:
    API_KEYS = st.secrets["API_KEYS"]
except:
    st.error("Brak listy API_KEYS w secrets!")
    st.stop()

if "key_index" not in st.session_state: st.session_state.key_index = 0

def get_current_key():
    return API_KEYS[st.session_state.key_index]

def rotate_key():
    st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)
    return st.session_state.key_index

# ==========================================
# 🚀 KONFIGURACJA MODELU
# ==========================================
MODEL_NAME = "gemini-3-pro-preview"
TEMPERATURE = 0.0

# Inicjalizacja stanu
if "operator" not in st.session_state: st.session_state.operator = ""
if "grupa" not in st.session_state: st.session_state.grupa = ""
if "messages" not in st.session_state: st.session_state.messages = []
if "chat_started" not in st.session_state: st.session_state.chat_started = False
if "current_start_pz" not in st.session_state: st.session_state.current_start_pz = None

with st.sidebar:
    st.title("⚙️ Panel Sterowania")
    
    # Wyświetlanie aktywnego modelu
    st.info(f"🧠 Model: **{MODEL_NAME}**")
    st.caption(f"🌡️ Temperatura: `{TEMPERATURE}`")
    st.caption(f"🔑 Aktywny klucz: {st.session_state.key_index + 1} / {len(API_KEYS)}")
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
    if st.button("🚀 Uruchom Czat", type="primary"):
        if not st.session_state.operator or not st.session_state.grupa:
            st.error("Wybierz Operatora i Grupę!")
        else:
            st.session_state.messages = []
            st.session_state.chat_started = True
            st.session_state.current_start_pz = None
            st.rerun()

    if st.button("🗑️ Reset Sesji"):
        st.session_state.clear()
        st.rerun()

# --- GŁÓWNY INTERFEJS ---
st.title(f"🤖 Szturchacz")

if not st.session_state.chat_started:
    st.info("👈 Skonfiguruj panel i kliknij 'Uruchom Czat'.")
else:
    SYSTEM_INSTRUCTION_BASE = st.secrets["SYSTEM_PROMPT"]
    parametry_startowe = f"\ndomyslny_operator={st.session_state.operator}\ndomyslna_data={datetime.now().strftime('%d.%m')}\nGrupa_Operatorska={st.session_state.grupa}\ndomyslny_tryb={wybrany_tryb_kod}"
    FULL_PROMPT = SYSTEM_INSTRUCTION_BASE + parametry_startowe

    st.title(f"🤖 Szturchacz ({st.session_state.operator} / {st.session_state.grupa})")

    # --- LOGIKA WYWOŁANIA API Z ROTACJĄ ---
    def call_gemini_with_rotation(history, user_input):
        max_retries = len(API_KEYS)
        attempts = 0
        
        while attempts < max_retries:
            try:
                genai.configure(api_key=get_current_key())
                model = genai.GenerativeModel(
                    model_name=MODEL_NAME,
                    system_instruction=FULL_PROMPT,
                    generation_config={"temperature": TEMPERATURE}
                )
                chat = model.start_chat(history=history)
                response = chat.send_message(user_input)
                return response.text, True
            
            except Exception as e:
                if isinstance(e, google_exceptions.ResourceExhausted) or "429" in str(e) or "Quota" in str(e):
                    attempts += 1
                    rotate_key()
                    st.toast(f"🔄 Limit klucza {attempts} wyczerpany. Przełączam na kolejny...")
                    time.sleep(1)
                else:
                    return f"Błąd API: {str(e)}", False
        
        return "❌ Wszystkie klucze API wyczerpane. Spróbuj ponownie za chwilę.", False

    # Autostart
    if len(st.session_state.messages) == 0:
        with st.spinner("Inicjalizacja..."):
            res_text, success = call_gemini_with_rotation([], "start")
            if success:
                st.session_state.messages.append({"role": "model", "content": res_text})
                st.rerun()
            else:
                st.error(res_text)

    # Wyświetlanie historii
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])
    
    # Obsługa wejścia użytkownika
    if prompt := st.chat_input("Wklej wsad..."):
        # Ustalanie PZ startowego
        input_pz = parse_pz(prompt)
        if st.session_state.current_start_pz is None:
             st.session_state.current_start_pz = input_pz if input_pz else "PZ_START"
        
        with st.chat_message("user"): st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        with st.chat_message("model"):
            with st.spinner("Analizuję..."):
                # Budowanie historii dla API
                history_api = [{"role": "user", "parts": ["start"]}] + [{"role": m["role"], "parts": [m["content"]]} for m in st.session_state.messages[:-1]]
                
                res_text, success = call_gemini_with_rotation(history_api, prompt)
                
                if success:
                    st.markdown(res_text)
                    st.session_state.messages.append({"role": "model", "content": res_text})
                    
                    # Logowanie statystyk po wykryciu końca sesji
                    if 'cop#' in res_text.lower() and 'c#' in res_text.lower():
                        end_pz = parse_pz(res_text)
                        log_session_and_transition(st.session_state.operator, st.session_state.current_start_pz, end_pz if end_pz else "PZ_END")
                        st.session_state.current_start_pz = None
                else:
                    st.error(res_text)
