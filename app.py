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

# --- 0. KONFIGURACJA ŚRODOWISKA ---
st.set_page_config(page_title="Szturchacz AI", layout="wide")
try:
    locale.setlocale(locale.LC_TIME, "pl_PL.UTF-8")
except: pass

# --- INICJALIZACJA BAZY DANYCH ---
if "db_status" not in st.session_state: st.session_state.db_status = "Inicjalizacja..."
try:
    if not firebase_admin._apps:
        creds_dict = json.loads(st.secrets["FIREBASE_CREDS"])
        creds = credentials.Certificate(creds_dict)
        firebase_admin.initialize_app(creds)
    db = firestore.client()
    st.session_state.db_status = "✅ Połączono z Firestore"
except Exception as e:
    st.session_state.db_status = f"❌ Błąd bazy: {str(e)}"

# --- FUNKCJE POMOCNICZE ---
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
    
    status_msg = "Próba zapisu..."
    
    try:
        doc_ref = db.collection("stats").document(today_str).collection("operators").document(operator_name)
        
        # 1. Zapisz sesję
        update_data = {"sessions_completed": firestore.Increment(1)}
        
        # 2. Zapisz przejście (jeśli jest postęp)
        start_val = get_pz_value(start_pz)
        end_val = get_pz_value(end_pz)
        
        if start_val is not None and end_val is not None and end_val > start_val:
             transition_key = f"pz_transitions.{start_pz}_to_{end_pz}"
             update_data[transition_key] = firestore.Increment(1)
             status_msg = f"✅ Zapisano sesję + przejście ({start_pz}->{end_pz})"
        else:
             status_msg = f"✅ Zapisano tylko sesję (brak postępu PZ)"

        doc_ref.set(update_data, merge=True)
        
        # 3. WERYFIKACJA (Odczyt kontrolny)
        new_data = doc_ref.get().to_dict()
        current_count = new_data.get("sessions_completed", 0)
        status_msg += f" | Licznik w bazie: {current_count}"
        
    except Exception as e:
        status_msg = f"❌ Błąd zapisu: {str(e)}"
    
    return status_msg

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
    return False

if not check_password(): st.stop()

# ==========================================
# 🔑 STAN APLIKACJI
# ==========================================
if "key_index" not in st.session_state: st.session_state.key_index = 0
if "is_fallback" not in st.session_state: st.session_state.is_fallback = False
if "operator" not in st.session_state: st.session_state.operator = ""
if "grupa" not in st.session_state: st.session_state.grupa = ""
if "selected_model_label" not in st.session_state: st.session_state.selected_model_label = "Gemini 3.0 Pro"
if "messages" not in st.session_state: st.session_state.messages = []
if "chat_started" not in st.session_state: st.session_state.chat_started = False
if "cache_handle" not in st.session_state: st.session_state.cache_handle = None
# Zmienne diagnostyczne
if "last_log_status" not in st.session_state: st.session_state.last_log_status = "Brak akcji"
if "last_trigger_check" not in st.session_state: st.session_state.last_trigger_check = "Oczekiwanie..."

try:
    API_KEYS = st.secrets["API_KEYS"]
except:
    st.error("Brak API_KEYS!")
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
    "Gemini 1.5 Pro (2.5)": "gemini-1.5-pro"
}
TEMPERATURE = 0.0

with st.sidebar:
    st.title("⚙️ Panel Sterowania")
    st.info(st.session_state.db_status) # Status bazy na górze
    
    st.radio("Model:", list(MODEL_MAP.keys()), key="selected_model_label")
    st.selectbox("Operator:", ["", "Emilia", "Oliwia", "Iwona", "Marlena", "Magda", "Sylwia", "Ewelina", "Klaudia"], key="operator")
    st.selectbox("Grupa:", ["", "Operatorzy_DE", "Operatorzy_FR", "Operatorzy_UK/PL"], key="grupa")
    st.selectbox("Tryb:", ["Standard", "Kanał"], key="tryb_label")
    
    st.markdown("---")
    if st.button("🚀 Uruchom Czat", type="primary"):
        if not st.session_state.operator or not st.session_state.grupa:
            st.error("Wybierz Operatora i Grupę!")
        else:
            st.session_state.messages = []
            st.session_state.chat_started = True
            st.session_state.cache_handle = None
            if st.session_state.selected_model_label == "Gemini 3.0 Pro":
                st.session_state.is_fallback = False
            st.rerun()

    if st.button("🗑️ Reset"):
        st.session_state.clear()
        st.rerun()
        
    st.markdown("---")
    st.markdown("### 🕵️ DIAGNOSTYKA")
    st.caption("Co widzi system w ostatniej odpowiedzi:")
    st.code(st.session_state.last_trigger_check, language="text")
    st.caption("Status zapisu:")
    st.code(st.session_state.last_log_status, language="text")

st.title(f"🤖 Szturchacz")

if not st.session_state.chat_started:
    st.info("👈 Wybierz parametry i kliknij **'Uruchom Czat'**.")
else:
    SYSTEM_INSTRUCTION_BASE = st.secrets["SYSTEM_PROMPT"]
    # Uproszczony wybór trybu dla kodu
    tryb_kod = "od_szturchacza" if st.session_state.tryb_label == "Standard" else "kanal"
    
    parametry_startowe = f"""
# PARAMETRY STARTOWE
domyslny_operator={st.session_state.operator}
domyslna_data={datetime.now().strftime("%d.%m")}
Grupa_Operatorska={st.session_state.grupa}
domyslny_tryb={tryb_kod}
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

    st.title(f"🤖 Szturchacz ({st.session_state.operator})")

    if len(st.session_state.messages) == 0:
        with st.spinner("Start..."):
            try:
                m = get_or_create_model(MODEL_MAP[st.session_state.selected_model_label], FULL_PROMPT)
                resp = m.start_chat().send_message("start")
                st.session_state.messages.append({"role": "model", "content": resp.text})
            except Exception as e:
                st.error(f"Błąd: {e}")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    if prompt := st.chat_input("Wklej wsad..."):
        start_pz = parse_pz(prompt)
        st.session_state.current_start_pz = start_pz if start_pz else "PZ_START"
        
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
                    
                    # --- DIAGNOSTYKA TRIGGERA ---
                    has_cop = 'cop#' in response_text.lower()
                    has_c = 'c#' in response_text.lower()
                    
                    st.session_state.last_trigger_check = f"""
                    COP# wykryto: {has_cop}
                    C# wykryto: {has_c}
                    PZ start: {st.session_state.current_start_pz}
                    PZ koniec (surowy): {parse_pz(response_text)}
                    """
                    
                    if has_cop and has_c:
                        end_pz = parse_pz(response_text)
                        if not end_pz: end_pz = "PZ_END"
                        
                        # Logujemy i zapisujemy wynik w sesji, żeby wyświetlić w sidebarze
                        status = log_session_and_transition(
                            st.session_state.operator, 
                            st.session_state.current_start_pz, 
                            end_pz
                        )
                        st.session_state.last_log_status = status
                        st.rerun() # Odświeżamy, żeby zaktualizować sidebar
