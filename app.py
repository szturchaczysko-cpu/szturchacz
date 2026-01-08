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
import firebase_admin
from firebase_admin import credentials, firestore
from streamlit_cookies_manager import EncryptedCookieManager

# --- 0. KONFIGURACJA ŚRODOWISKA ---
st.set_page_config(page_title="Szturchacz AI", layout="wide")
try:
    locale.setlocale(locale.LC_TIME, "pl_PL.UTF-8")
except: pass

# --- MENEDŻER CIASTECZEK (PRZYWRÓCONY) ---
# Wymaga COOKIE_PASSWORD w secrets.toml
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

# --- FUNKCJE DO STATYSTYK ---
def parse_pz(text):
    if not text: return None
    # Szuka PZ+cyfra (np. PZ0, PZ12)
    match = re.search(r'(PZ\d+)', text, re.IGNORECASE)
    if match: return match.group(1).upper()
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
        
        # 1. Zapisz sesję (Zawsze)
        update_data = {"sessions_completed": firestore.Increment(1)}
        
        # 2. Zapisz przejście (Tylko jeśli jest postęp)
        start_val = get_pz_value(start_pz)
        end_val = get_pz_value(end_pz)
        
        if start_val is not None and end_val is not None and end_val > start_val:
             transition_key = f"pz_transitions.{start_pz}_to_{end_pz}"
             update_data[transition_key] = firestore.Increment(1)

        doc_ref.set(update_data, merge=True)
    except Exception:
        pass

# ==========================================
# 🔒 BRAMKA BEZPIECZEŃSTWA (Z ZAPAMIĘTYWANIEM)
# ==========================================
def check_password():
    # 1. Sprawdź sesję bieżącą
    if st.session_state.get("password_correct"):
        return True
    
    # 2. Sprawdź ciasteczko (dla Chrome)
    if cookies.get("password_correct") == "true":
        st.session_state.password_correct = True
        return True

    st.header("🔒 Dostęp chroniony (Szturchacz)")
    password_input = st.text_input("Podaj hasło dostępu:", type="password", key="password_input")
    
    if st.button("Zaloguj"):
        if st.session_state.password_input == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            # Zapisz w ciasteczku
            cookies['password_correct'] = 'true'
            cookies.save()
            st.rerun()
        else:
            st.error("😕 Błędne hasło")
    return False

if not check_password():
    st.stop()

# ==========================================
# 🔑 INICJALIZACJA STANU APLIKACJI
# ==========================================
if "key_index" not in st.session_state: st.session_state.key_index = 0
if "is_fallback" not in st.session_state: st.session_state.is_fallback = False
# Próba odczytu z ciasteczek, jeśli brak w sesji
if "operator" not in st.session_state: st.session_state.operator = cookies.get("operator", "")
if "grupa" not in st.session_state: st.session_state.grupa = cookies.get("grupa", "")
if "selected_model_label" not in st.session_state: st.session_state.selected_model_label = cookies.get("selected_model_label", "Gemini 3.0 Pro")

if "messages" not in st.session_state: st.session_state.messages = []
if "chat_started" not in st.session_state: st.session_state.chat_started = False
if "cache_handle" not in st.session_state: st.session_state.cache_handle = None
if "current_start_pz" not in st.session_state: st.session_state.current_start_pz = None

try:
    API_KEYS = st.secrets["API_KEYS"]
except:
    st.error("Brak listy API_KEYS w secrets!")
    st.stop()

def get_current_key(): return API_KEYS[st.session_state.key_index]
def rotate_key(): st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)

if st.session_state.is_fallback:
    st.markdown("""<style>[data-testid="stSidebar"] {background-color: #FF4B4B !important;} [data-testid="stSidebar"] * {color: white !important;}</style>""", unsafe_allow_html=True)

# ==========================================
# 🚀 APLIKACJA
# ==========================================
MODEL_MAP = {
    "Gemini 3.0 Pro": "gemini-3-pro-preview",
    "Gemini 1.5 Pro (2.5)": "gemini-2.5-pro"
}
TEMPERATURE = 0.0

with st.sidebar:
    if st.session_state.is_fallback:
        st.markdown("<h1 style='text-align: center; font-size: 80px;'>🦖😲</h1>", unsafe_allow_html=True)
        st.error("Limity 3.0 Pro wyczerpane! Działam na 1.5 Pro.")
    
    st.title("⚙️ Panel Sterowania")
    
    # Wybór modelu (zapisywany w ciasteczku przy uruchomieniu)
    st.radio("Model AI:", list(MODEL_MAP.keys()), key="selected_model_label")
    active_model_name = MODEL_MAP[st.session_state.selected_model_label]
    if st.session_state.is_fallback: active_model_name = MODEL_MAP["Gemini 1.5 Pro (2.5)"]
    
    st.caption(f"🧠 Model: `{active_model_name}`")
    st.caption(f"🌡️ Temp: `{TEMPERATURE}`")
    st.caption(f"🔑 Klucz: {st.session_state.key_index + 1}/{len(API_KEYS)}")
    st.markdown("---")
    
    st.selectbox("Operator:", ["", "Emilia", "Oliwia", "Iwona", "Marlena", "Magda", "Sylwia", "Ewelina", "Klaudia"], key="operator")
    st.selectbox("Grupa:", ["", "Operatorzy_DE", "Operatorzy_FR", "Operatorzy_UK/PL"], key="grupa")
    
    # --- PRZYWRÓCONE PEŁNE TRYBY ---
    TRYBY_DICT = {
        "Standard (Panel + Koperta)": "od_szturchacza",
        "WhatsApp (Rolka + Panel)": "WA",
        "E-mail (Rolka + Panel)": "MAIL",
        "Forum/Inne (Wpis + Panel)": "FORUM"
    }
    tryb_label = st.selectbox("Tryb Startowy:", list(TRYBY_DICT.keys()))
    wybrany_tryb_kod = TRYBY_DICT[tryb_label]
    
    st.markdown("---")
    if st.button("🚀 Uruchom Czat", type="primary"):
        if not st.session_state.operator or not st.session_state.grupa:
            st.error("Wybierz Operatora i Grupę!")
        else:
            # ZAPIS DO CIASTECZEK (Dla Chrome)
            cookies['operator'] = st.session_state.operator
            cookies['grupa'] = st.session_state.grupa
            cookies['selected_model_label'] = st.session_state.selected_model_label
            cookies.save()
            
            st.session_state.messages = []
            st.session_state.chat_started = True
            st.session_state.cache_handle = None
            st.session_state.current_start_pz = None
            
            if st.session_state.selected_model_label == "Gemini 3.0 Pro":
                st.session_state.is_fallback = False
            st.rerun()

    if st.button("🗑️ Reset Sesji"):
        st.session_state.clear()
        st.rerun()

st.title(f"🤖 Szturchacz")

if not st.session_state.chat_started:
    st.info("👈 Wybierz parametry i kliknij **'Uruchom Czat'**.")
else:
    SYSTEM_INSTRUCTION_BASE = st.secrets["SYSTEM_PROMPT"]
    parametry_startowe = f"""
# PARAMETRY STARTOWE
domyslny_operator={st.session_state.operator}
domyslna_data={datetime.now().strftime("%d.%m")}
Grupa_Operatorska={st.session_state.grupa}
domyslny_tryb={wybrany_tryb_kod}
"""
    FULL_PROMPT = SYSTEM_INSTRUCTION_BASE + "\n" + parametry_startowe

    def get_or_create_model(model_name, full_prompt):
        cache_key = f"cache_{hash(full_prompt)}"
        if st.session_state.get(cache_key):
            return genai.GenerativeModel.from_cached_content(st.session_state[cache_key])
        
        with st.spinner("Tworzenie cache..."):
            genai.configure(api_key=get_current_key())
            cache = caching.CachedContent.create(
                model=f'models/{model_name}',
                system_instruction=full_prompt,
                ttl=timedelta(hours=1)
            )
            st.session_state[cache_key] = cache
            return genai.GenerativeModel.from_cached_content(cache)

    st.title(f"🤖 Szturchacz ({st.session_state.operator} / {st.session_state.grupa})")

    # Autostart
    if len(st.session_state.messages) == 0:
        with st.spinner("Start..."):
            try:
                target_model = MODEL_MAP[st.session_state.selected_model_label]
                m = get_or_create_model(target_model, FULL_PROMPT)
                resp = m.start_chat().send_message("start")
                st.session_state.messages.append({"role": "model", "content": resp.text})
            except Exception as e:
                st.error(f"Błąd: {e}")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    if prompt := st.chat_input("Wklej wsad..."):
        # 1. PARSOWANIE PZ STARTOWEGO
        # Ustawiamy go TYLKO RAZ na początku sprawy (gdy jest None)
        input_pz = parse_pz(prompt)
        if st.session_state.current_start_pz is None:
             st.session_state.current_start_pz = input_pz if input_pz else "PZ_START"
        
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        with st.chat_message("model"):
            placeholder = st.empty()
            with st.spinner("Analizuję..."):
                history = [{"role": "user", "parts": ["start"]}] + [{"role": m["role"], "parts": [m["content"]]} for m in st.session_state.messages[:-1]]
                
                max_retries = len(API_KEYS)
                attempts = 0
                success = False
                target_model = MODEL_MAP[st.session_state.selected_model_label]
                if st.session_state.is_fallback: target_model = MODEL_MAP["Gemini 1.5 Pro (2.5)"]

                while attempts <= max_retries and not success:
                    try:
                        model = get_or_create_model(target_model, FULL_PROMPT)
                        response = model.start_chat(history=history).send_message(prompt)
                        response_text = response.text
                        success = True
                    except Exception as e:
                        if isinstance(e, google_exceptions.ResourceExhausted) or "429" in str(e):
                            attempts += 1
                            if attempts < max_retries:
                                rotate_key()
                                time.sleep(1)
                            else:
                                if target_model == MODEL_MAP["Gemini 3.0 Pro"] and not st.session_state.is_fallback:
                                    st.session_state.is_fallback = True
                                    st.session_state.cache_handle = None
                                    st.rerun()
                                else:
                                    st.error("❌ Limity wyczerpane!")
                                    break
                        else:
                            st.error(f"Błąd: {e}")
                            break
                
                if success:
                    placeholder.markdown(response_text)
                    st.session_state.messages.append({"role": "model", "content": response_text})
                    
                    # --- LOGIKA ZAPISU STATYSTYK ---
                    if 'cop#' in response_text.lower() and 'c#' in response_text.lower():
                        end_pz = parse_pz(response_text)
                        if not end_pz: end_pz = "PZ_END"
                        
                        log_session_and_transition(
                            st.session_state.operator, 
                            st.session_state.current_start_pz, 
                            end_pz
                        )
                        # Po zalogowaniu czyścimy PZ startowy
                        st.session_state.current_start_pz = None
