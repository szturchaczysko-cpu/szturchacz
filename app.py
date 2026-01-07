import streamlit as st
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from datetime import datetime
import locale
import time

# --- 0. KONFIGURACJA ŚRODOWISKA ---
st.set_page_config(page_title="Szturchacz AI", layout="wide")
try:
    locale.setlocale(locale.LC_TIME, "pl_PL.UTF-8")
except: pass

# ==========================================
# 🔒 BRAMKA BEZPIECZEŃSTWA
# ==========================================
def check_password():
    if st.session_state.get("password_correct"):
        return True
    st.header("🔒 Dostęp chroniony (Szturchacz)")
    password_input = st.text_input("Podaj hasło dostępu:", type="password", key="password_input")
    if st.button("Zaloguj"):
        if st.session_state.password_input == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
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
if "operator" not in st.session_state: st.session_state.operator = ""
if "grupa" not in st.session_state: st.session_state.grupa = ""
if "messages" not in st.session_state: st.session_state.messages = []
if "chat_started" not in st.session_state: st.session_state.chat_started = False
if "selected_model" not in st.session_state: st.session_state.selected_model = "Gemini 3.0 Pro"

try:
    API_KEYS = st.secrets["API_KEYS"]
except:
    st.error("Brak listy API_KEYS w secrets!")
    st.stop()

def get_current_key(): return API_KEYS[st.session_state.key_index]
def rotate_key(): st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)

# --- STYL DINOZAURA ---
if st.session_state.is_fallback:
    st.markdown("""<style>[data-testid="stSidebar"] {background-color: #FF4B4B !important;} [data-testid="stSidebar"] * {color: white !important;}</style>""", unsafe_allow_html=True)

# ==========================================
# 🚀 APLIKACJA
# ==========================================
MODEL_MAP = {
    "Gemini 3.0 Pro": "gemini-3-pro-preview",
    "Gemini 1.5 Pro (2.5)": "gemini-1.5-pro-latest"
}
TEMPERATURE = 0.0

# --- 1. PANEL BOCZNY ---
with st.sidebar:
    if st.session_state.is_fallback:
        st.markdown("<h1 style='text-align: center; font-size: 80px;'>🦖😲</h1>", unsafe_allow_html=True)
        st.error("Limity 3.0 Pro wyczerpane! Działam na 1.5 Pro.")
    
    st.title("⚙️ Panel Sterowania")
    
    # --- NOWY WYBÓR MODELU ---
    st.radio("Wybierz model AI:", list(MODEL_MAP.keys()), key="selected_model")
    active_model_name = MODEL_MAP[st.session_state.selected_model]
    
    st.caption(f"🧠 Model: `{active_model_name}`")
    st.caption(f"🌡️ Temp: `{TEMPERATURE}`")
    st.caption(f"🔑 Klucz: {st.session_state.key_index + 1}/{len(API_KEYS)}")
    st.markdown("---")

    st.subheader("👤 Operator")
    st.selectbox("Kto obsługuje?", ["", "Emilia", "Oliwia", "Iwona", "Marlena", "Magda", "Sylwia", "Ewelina", "Klaudia"], key="operator")

    st.subheader("🌐 Grupa Operatorska")
    st.selectbox("Do której grupy należysz?", ["", "Operatorzy_DE", "Operatorzy_FR", "Operatorzy_UK/PL"], key="grupa")

    st.subheader("📥 Tryb Startowy")
    wybrany_tryb_label = st.selectbox("Typ pierwszego wsadu?", {"Standard": "obecny", "Kanał": "kanal"}, key="tryb_label")
    wybrany_tryb_kod = {"Standard": "obecny", "Kanał": "kanal"}[st.session_state.tryb_label]
    
    st.markdown("---")
    
    if st.button("🚀 Uruchom / Przeładuj Czat", type="primary"):
        if not st.session_state.operator or not st.session_state.grupa:
            st.sidebar.error("Wybierz Operatora i Grupę!")
        else:
            st.session_state.messages = []
            st.session_state.chat_started = True # Od razu ustawiamy, że startujemy
            st.session_state.is_fallback = False # Resetujemy dinozaura przy starcie
            st.rerun()

    if st.button("🗑️ Resetuj Sesję"):
        st.session_state.clear()
        st.rerun()

# --- 2. GŁÓWNY INTERFEJS ---
st.title(f"🤖 Szturchacz")

if not st.session_state.chat_started:
    st.info("👈 Wybierz parametry w panelu bocznym i kliknij **'Uruchom / Przeładuj Czat'**.")
else:
    # --- PROMPT I KONFIGURACJA MODELU ---
    try:
        SYSTEM_INSTRUCTION_BASE = st.secrets["SYSTEM_PROMPT"]
    except:
        st.error("Brak SYSTEM_PROMPT w secrets!")
        st.stop()

    parametry_startowe = f"""
# PARAMETRY STARTOWE
domyslny_operator={st.session_state.operator}
domyslna_data={datetime.now().strftime("%d.%m")}
Grupa_Operatorska={st.session_state.grupa}
domyslny_tryb={wybrany_tryb_kod}
"""
    FULL_PROMPT = SYSTEM_INSTRUCTION_BASE + "\n" + parametry_startowe

    def create_model(model_name):
        genai.configure(api_key=get_current_key())
        return genai.GenerativeModel(model_name=model_name, system_instruction=FULL_PROMPT,
                                     generation_config={"temperature": TEMPERATURE})

    st.title(f"🤖 Szturchacz ({st.session_state.operator} / {st.session_state.grupa})")

    # Autostart
    if len(st.session_state.messages) == 0:
        with st.spinner("Inicjalizacja systemu..."):
            try:
                model_to_start = MODEL_MAP[st.session_state.selected_model]
                m = create_model(model_to_start)
                response = m.start_chat().send_message("start")
                st.session_state.messages.append({"role": "model", "content": response.text})
            except Exception as e:
                st.error(f"Błąd startu: {e}")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    # --- GŁÓWNA PĘTLA ---
    if prompt := st.chat_input("Wklej wsad..."):
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
                
                # Ustawiamy model docelowy i awaryjny
                target_model_name = MODEL_MAP[st.session_state.selected_model]
                fallback_model_name = MODEL_MAP["Gemini 1.5 Pro (2.5)"]

                # Jeśli już jesteśmy w trybie awaryjnym, od razu używamy 1.5 Pro
                if st.session_state.is_fallback:
                    target_model_name = fallback_model_name

                while attempts <= max_retries and not success:
                    try:
                        genai.configure(api_key=get_current_key())
                        model = create_model(target_model_name)
                        response = model.start_chat(history=history).send_message(prompt)
                        placeholder.markdown(response.text)
                        st.session_state.messages.append({"role": "model", "content": response.text})
                        success = True
                    except Exception as e:
                        if isinstance(e, google_exceptions.ResourceExhausted) or "429" in str(e):
                            attempts += 1
                            if attempts < max_retries:
                                rotate_key()
                                placeholder.warning(f"Zmiana klucza ({attempts}/{max_retries})...")
                                time.sleep(1)
                            else:
                                # Jeśli skończyły się klucze dla 3.0 Pro, przechodzimy na 1.5 Pro
                                if target_model_name == MODEL_MAP["Gemini 3.0 Pro"] and not st.session_state.is_fallback:
                                    st.session_state.is_fallback = True
                                    placeholder.error("⚠️ Limity 3.0 Pro wyczerpane! Przechodzę w tryb DINOZAURA (1.5 Pro)...")
                                    time.sleep(2)
                                    st.rerun()
                                else:
                                    # Jeśli padły limity nawet dla 1.5 Pro
                                    st.error("❌ Wszystkie klucze i modele awaryjne wyczerpane!")
                                    break
                        else:
                            st.error(f"Błąd: {e}")
                            break
