import streamlit as st
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from PIL import Image
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
# 🔑 MENEDŻER KLUCZY I INICJALIZACJA STANU
# ==========================================
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

try:
    API_KEYS = st.secrets["API_KEYS"]
except:
    st.error("Brak listy API_KEYS w secrets!")
    st.stop()

def get_current_key():
    return API_KEYS[st.session_state.key_index]

def rotate_key():
    st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)
    return st.session_state.key_index

# --- STYL DINOZAURA ---
if st.session_state.is_fallback:
    st.markdown("""<style>[data-testid="stSidebar"] {background-color: #FF4B4B !important;} [data-testid="stSidebar"] * {color: white !important;}</style>""", unsafe_allow_html=True)

# ==========================================
# 🚀 APLIKACJA
# ==========================================
MODEL_PRO = "gemini-3-pro-preview"
MODEL_FLASH = "gemini-3-flash-preview"
TEMPERATURE = 0.0

# --- 1. PANEL BOCZNY ---
with st.sidebar:
    if st.session_state.is_fallback:
        st.markdown("<h1 style='text-align: center; font-size: 80px;'>🦖😲</h1>", unsafe_allow_html=True)
        st.error("Limity PRO wyczerpane! Działam na FLASH.")
    
    st.title("⚙️ Panel Sterowania")
    st.caption(f"🧠 Model: `{MODEL_FLASH if st.session_state.is_fallback else MODEL_PRO}`")
    st.caption(f"🔑 Klucz: {st.session_state.key_index + 1}/{len(API_KEYS)}")
    st.markdown("---")

    # --- POPRAWIONA LOGIKA WYBORU ---
    # Używamy `key` do powiązania ze stanem sesji
    st.subheader("👤 Operator")
    st.selectbox("Kto obsługuje?", ["", "Emilia", "Oliwia", "Iwona", "Marlena", "Magda", "Sylwia", "Ewelina", "Klaudia"], key="operator")

    st.subheader("🌐 Grupa Operatorska")
    st.selectbox("Do której grupy należysz?", ["", "Operatorzy_DE", "Operatorzy_FR", "Operatorzy_UK/PL"], key="grupa")

    st.subheader("📥 Tryb Startowy")
    wybrany_tryb_label = st.selectbox("Typ pierwszego wsadu?", {"Standard": "obecny", "Kanał": "kanal"}, key="tryb_label")
    wybrany_tryb_kod = {"Standard": "obecny", "Kanał": "kanal"}[st.session_state.tryb_label]
    
    st.markdown("---")
    uploaded_file = st.file_uploader("Dodaj zdjęcie", type=["jpg", "jpeg", "png"])
    if uploaded_file: st.image(uploaded_file)
    
    st.markdown("---")
    if st.button("🗑️ Resetuj rozmowę"):
        st.session_state.messages = []
        st.session_state.is_fallback = False
        st.rerun()

# --- 2. GŁÓWNA BRAMKA KONTROLNA (NAPRAWIONA) ---
# Sprawdzamy, czy stary wybór jest inny niż nowy
if "last_config" not in st.session_state:
    st.session_state.last_config = (st.session_state.operator, st.session_state.grupa)

if st.session_state.last_config != (st.session_state.operator, st.session_state.grupa):
    st.session_state.messages = []
    st.session_state.last_config = (st.session_state.operator, st.session_state.grupa)

# Blokada, jeśli którekolwiek pole jest puste.
if not st.session_state.operator or not st.session_state.grupa:
    st.info("👈 Proszę wybrać **Operatora** oraz **Grupę Operatorską**, aby rozpocząć.")
    st.stop()
else:
    # --- PROMPT I KONFIGURACJA MODELU ---
    try:
        SYSTEM_INSTRUCTION_BASE = st.secrets["SYSTEM_PROMPT"]
    except:
        st.error("Brak SYSTEM_PROMPT w secrets!")
        st.stop()

    now = datetime.now()
    parametry_startowe = f"""
# PARAMETRY STARTOWE
domyslny_operator={st.session_state.operator}
domyslna_data={now.strftime("%d.%m")}
Grupa_Operatorska={st.session_state.grupa}
domyslny_tryb={wybrany_tryb_kod}
"""
    FULL_PROMPT = SYSTEM_INSTRUCTION_BASE + "\n" + parametry_startowe

    def create_model(model_name):
        genai.configure(api_key=get_current_key())
        return genai.GenerativeModel(model_name=model_name, system_instruction=FULL_PROMPT)

    # --- 5. INTERFEJS CZATU ---
    st.title(f"🤖 Szturchacz ({st.session_state.operator} / {st.session_state.grupa})")
    
    # Autostart uruchomi się TUTAJ, gdy skrypt nie zostanie zatrzymany
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
            else:
                 st.error(f"Błąd startu: {e}")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    # --- 6. GŁÓWNA PĘTLA ---
    if prompt := st.chat_input("Wklej wsad..."):
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        with st.chat_message("model"):
            placeholder = st.empty()
            with st.spinner("Analizuję..."):
                history = [{"role": "user", "parts": ["start"]}] + [{"role": m["role"], "parts": [m["content"]]} for m in st.session_state.messages[:-1]]
                content = [prompt, Image.open(uploaded_file)] if uploaded_file else prompt
                
                # ... (reszta pętli rotatora - bez zmian)
                max_retries = len(API_KEYS)
                attempts = 0
                success = False
                target_model = MODEL_FLASH if st.session_state.is_fallback else MODEL_PRO

                while attempts < max_retries and not success:
                    try:
                        genai.configure(api_key=get_current_key())
                        model = create_model(target_model)
                        chat = model.start_chat(history=history)
                        response = chat.send_message(content)
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
                                if not st.session_state.is_fallback:
                                    st.session_state.is_fallback = True
                                    placeholder.error("Przechodzę w tryb DINOZAURA!")
                                    time.sleep(2)
                                    st.rerun()
                        else:
                            st.error(f"Błąd: {e}")
                            break
