import streamlit as st
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from PIL import Image
from datetime import datetime
import locale
import time

# --- 0. KONFIGURACJA ŚRODOWISKA ---
try:
    locale.setlocale(locale.LC_TIME, "pl_PL.UTF-8")
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, "pl_PL")
    except:
        pass

st.set_page_config(page_title="Szturchacz AI", layout="wide")

# ==========================================
# 🔒 BRAMKA BEZPIECZEŃSTWA
# ==========================================
def check_password():
    if st.session_state.get("password_correct", False):
        return True
    st.header("🔒 Dostęp chroniony (Szturchacz)")
    password_input = st.text_input("Podaj hasło dostępu:", type="password")
    if st.button("Zaloguj"):
        if password_input == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("😕 Błędne hasło")
    return False

if not check_password():
    st.stop()

# ==========================================
# 🔑 MENEDŻER KLUCZY I TRYB AWARYJNY (DINO)
# ==========================================
try:
    API_KEYS = st.secrets["API_KEYS"]
    if not isinstance(API_KEYS, list):
        API_KEYS = [API_KEYS]
except Exception:
    st.error("🚨 Błąd: Brak 'API_KEYS' w secrets.toml")
    st.stop()

# --- KLUCZOWE ZMIANY W SESSION_STATE ---
# Inicjalizujemy stan tylko raz, na samym początku sesji
if "key_index" not in st.session_state:
    st.session_state.key_index = 0
if "is_fallback" not in st.session_state:
    st.session_state.is_fallback = False
if "operator" not in st.session_state:
    st.session_state.operator = ""
if "grupa" not in st.session_state:
    st.session_state.grupa = ""
if "messages" not in st.session_state:
    st.session_state.messages = []

def get_current_key():
    return API_KEYS[st.session_state.key_index]

def rotate_key():
    st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)
    return st.session_state.key_index

if st.session_state.is_fallback:
    st.markdown("""
        <style>
        [data-testid="stSidebar"] {
            background-color: #FF4B4B !important;
        }
        [data-testid="stSidebar"] * {
            color: white !important;
        }
        </style>
    """, unsafe_allow_html=True)

# ==========================================
# 🚀 APLIKACJA
# ==========================================
MODEL_PRO = "gemini-3-pro-preview"
MODEL_FLASH = "gemini-3-flash-preview"
TEMPERATURE = 0.0

# --- 1. PANEL BOCZNY ---
DOSTEPNI_OPERATORZY = ["", "Emilia", "Oliwia", "Iwona", "Marlena", "Magda", "Sylwia", "Ewelina", "Klaudia"]
TRYBY_WSADU = {"Standard": "obecny", "Kanał (WA/Mail/...)": "kanal"}
GRUPY_OPERATORSKIE = ["", "Operatorzy_DE", "Operatorzy_FR", "Operatorzy_UK/PL"]

with st.sidebar:
    if st.session_state.is_fallback:
        st.markdown("<h1 style='text-align: center; font-size: 80px;'>🦖😲</h1>", unsafe_allow_html=True)
        st.error("Limity PRO wyczerpane! Działam na FLASH.")
        st.markdown("---")

    st.title("⚙️ Panel Sterowania")
    st.caption(f"🧠 Model: `{MODEL_FLASH if st.session_state.is_fallback else MODEL_PRO}`")
    st.caption(f"🌡️ Temp: `{TEMPERATURE}`")
    st.caption(f"🔑 Klucz: {st.session_state.key_index + 1}/{len(API_KEYS)}")
    st.markdown("---")

    # --- ZMIANA: Powiązanie selectboxów z session_state za pomocą parametru `key` ---
    st.subheader("👤 Operator")
    st.selectbox("Kto obsługuje?", DOSTEPNI_OPERATORZY, key="operator")

    st.subheader("🌐 Grupa Operatorska")
    st.selectbox("Do której grupy należysz?", GRUPY_OPERATORSKIE, key="grupa")

    st.subheader("📥 Tryb Startowy")
    wybrany_tryb_label = st.selectbox("Jakiego typu jest pierwszy wsad?", list(TRYBY_WSADU.keys()), index=0)
    wybrany_tryb_kod = TRYBY_WSADU[wybrany_tryb_label]
    
    st.markdown("---")
    st.subheader("📸 Załącznik")
    uploaded_file = st.file_uploader("Dodaj zdjęcie/zrzut", type=["jpg", "jpeg", "png"])
    if uploaded_file:
        st.image(uploaded_file, caption="Podgląd", use_container_width=True)

    st.markdown("---")
    if st.button("🗑️ Resetuj rozmowę"):
        st.session_state.messages = []
        st.session_state.is_fallback = False
        st.rerun()

# --- 2. LOGIKA STANU I WALIDACJA (NAPRAWIONA) ---
# Sprawdzamy, czy stary wybór jest inny niż nowy, żeby zresetować czat
if "last_config" not in st.session_state:
    st.session_state.last_config = (st.session_state.operator, st.session_state.grupa)

if st.session_state.last_config != (st.session_state.operator, st.session_state.grupa):
    st.session_state.messages = []
    st.session_state.last_config = (st.session_state.operator, st.session_state.grupa)

# Blokada, jeśli którekolwiek pole jest puste.
if not st.session_state.operator or not st.session_state.grupa:
    st.info("👈 Proszę wybrać **Operatora** oraz **Grupę Operatorską**, aby rozpocząć.")
    st.stop()

# --- 3. PROMPT I PARAMETRY ---
try:
    SYSTEM_INSTRUCTION_BASE = st.secrets["SYSTEM_PROMPT"]
except Exception:
    st.error("🚨 Brak SYSTEM_PROMPT w secrets!")
    st.stop()

now = datetime.now()
parametry_startowe = f"""
# PARAMETRY STARTOWE
domyslny_operator={st.session_state.operator}
domyslna_data={now.strftime("%d.%m")}
Grupa_Operatorska={st.session_state.grupa}
domyslny_tryb={wybrany_tryb_kod}
godziny_fedex='8-16:30'
godziny_ups='8-18'
"""

FULL_PROMPT = SYSTEM_INSTRUCTION_BASE + "\n" + parametry_startowe

# --- 4. FUNKCJA TWORZENIA MODELU ---
def create_model(model_name):
    genai.configure(api_key=get_current_key())
    return genai.GenerativeModel(
        model_name=model_name,
        generation_config={"temperature": TEMPERATURE, "max_output_tokens": 8192},
        system_instruction=FULL_PROMPT
    )

# --- 5. INTERFEJS CZATU ---
st.title(f"🤖 Szturchacz ({st.session_state.operator} / {st.session_state.grupa})")

# Autostart
if len(st.session_state.messages) == 0:
    try:
        with st.spinner("Inicjalizacja systemu..."):
            m = create_model(MODEL_PRO)
            chat_init = m.start_chat(history=[])
            response_init = chat_init.send_message("start")
            st.session_state.messages.append({"role": "model", "content": response_init.text})
    except Exception as e:
        if "429" in str(e) or "Quota" in str(e):
             st.session_state.is_fallback = True
             st.rerun()
        st.error(f"Błąd startu: {e}")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- 6. GŁÓWNA PĘTLA ---
if prompt := st.chat_input("Wklej wsad..."):
    # (reszta pętli bez zmian - kopiuję dla kompletności)
    with st.chat_message("user"):
        st.markdown(prompt)
        if uploaded_file:
            st.image(Image.open(uploaded_file), width=300)
            
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("model"):
        placeholder = st.empty()
        with st.spinner("Analizuję..."):
            
            history_for_api = [{"role": "user", "parts": ["start"]}]
            for m in st.session_state.messages[:-1]:
                history_for_api.append({"role": m["role"], "parts": [m["content"]]})
            
            content_to_send = [prompt, Image.open(uploaded_file)] if uploaded_file else prompt

            max_retries = len(API_KEYS)
            attempts = 0
            success = False
            response_text = ""
            target_model = MODEL_FLASH if st.session_state.is_fallback else MODEL_PRO

            while attempts < max_retries and not success:
                try:
                    genai.configure(api_key=get_current_key())
                    current_model = create_model(target_model)
                    chat = current_model.start_chat(history=history_for_api)
                    response = chat.send_message(content_to_send)
                    response_text = response.text
                    success = True
                
                except Exception as e:
                    if isinstance(e, google_exceptions.ResourceExhausted) or "429" in str(e):
                        attempts += 1
                        if attempts < max_retries:
                            rotate_key()
                            placeholder.warning(f"Zmiana klucza ({attempts}/{max_retries})...")
                            time.sleep(1)
                        else:
                            if not st.session_state.is_fallback:
                                st.session_state.is_fallback = True
                                placeholder.error("⚠️ Przechodzę w tryb DINOZAURA (Flash)...")
                                time.sleep(2)
                                st.rerun() 
                    else:
                        st.error(f"Błąd: {e}")
                        break
            
            if success:
                placeholder.markdown(response_text)
                st.session_state.messages.append({"role": "model", "content": response_text})
