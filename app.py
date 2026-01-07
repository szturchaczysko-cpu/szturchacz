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

if "key_index" not in st.session_state:
    st.session_state.key_index = 0
if "is_fallback" not in st.session_state:
    st.session_state.is_fallback = False

def get_current_key():
    return API_KEYS[st.session_state.key_index]

def rotate_key():
    st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)
    return st.session_state.key_index

# --- 🦖 CSS DLA CZERWONEGO PANELU ---
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
# 🚀 KONFIGURACJA MODELI
# ==========================================
MODEL_PRO = "gemini-3-pro-preview"
MODEL_FLASH = "gemini-2.5-pro"
TEMPERATURE = 0.0

# --- 1. PANEL BOCZNY ---
DOSTEPNI_OPERATORZY = ["", "Emilia", "Oliwia", "Iwona", "Marlena", "Magda", "Sylwia", "Ewelina", "Klaudia"]
TRYBY_WSADU = {
    "Standard (Panel + Koperta)": "od_szturchacza",
    "WhatsApp (Rolka + Panel)": "WA",
    "E-mail (Rolka + Panel)": "MAIL",
    "Forum/Inne (Wpis + Panel)": "FORUM"
}

with st.sidebar:
    # Ikona dinozaura tylko w trybie awaryjnym
    if st.session_state.is_fallback:
        st.markdown("<h1 style='text-align: center; font-size: 80px; margin-bottom: 0;'>🦖😲</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; font-weight: bold;'>OJOJOJ! DINOZAUR!</p>", unsafe_allow_html=True)
        st.error("Limity PRO wyczerpane! Działam na darmowym FLASH.")
        st.markdown("---")

    st.title("⚙️ Panel Sterowania")
    st.caption(f"🧠 Model: `{MODEL_FLASH if st.session_state.is_fallback else MODEL_PRO}`")
    st.caption(f"🌡️ Temp: `{TEMPERATURE}`")
    st.caption(f"🔑 Klucz: {st.session_state.key_index + 1}/{len(API_KEYS)}")
    
    st.markdown("---")
    st.subheader("👤 Operator")
    wybrany_operator = st.selectbox("Kto obsługuje?", DOSTEPNI_OPERATORZY, index=0)

    st.subheader("📥 Tryb Wsadu")
    wybrany_tryb_label = st.selectbox("Skąd pochodzi wsad?", list(TRYBY_WSADU.keys()), index=0)
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

# --- 2. LOGIKA STANU ---
if "last_operator" not in st.session_state:
    st.session_state.last_operator = wybrany_operator

if st.session_state.last_operator != wybrany_operator:
    st.session_state.messages = []
    st.session_state.last_operator = wybrany_operator
    st.rerun()

if not wybrany_operator:
    st.info("👈 Wybierz operatora, aby rozpocząć.")
    st.stop()

# --- 3. PROMPT I PARAMETRY ---
try:
    SYSTEM_INSTRUCTION_BASE = st.secrets["SYSTEM_PROMPT"]
except Exception:
    st.error("🚨 Brak SYSTEM_PROMPT w secrets!")
    st.stop()

SECTION_14_OVERRIDE = """
*** AKTUALIZACJA LOGIKI STARTOWEJ (NADPISUJE SEKCJĘ 14) ***
14. START (ZMODYFIKOWANA LOGIKA TRYBÓW)
Gdy instancja jest uruchamiana bez WSADU sprawy (komenda „start”):
1. Sprawdź parametr `domyslny_tryb`.
2. Przywitaj `domyslny_operator`.
3. Poproś o WSAD STARTOWY zależnie od trybu.
"""

now = datetime.now()
parametry_startowe = f"""
# PARAMETRY STARTOWE
domyslny_operator={wybrany_operator}
domyslna_data={now.strftime("%d.%m")}
kontekst_daty='{now.strftime("%A, %d.%m.%Y")}'
domyslny_tryb={wybrany_tryb_kod}
godziny_fedex='8-16:30'
godziny_ups='8-18'
"""

FULL_PROMPT = SYSTEM_INSTRUCTION_BASE + "\n\n" + SECTION_14_OVERRIDE + "\n" + parametry_startowe

# --- 4. FUNKCJA TWORZENIA MODELU ---
def create_model(model_name):
    genai.configure(api_key=get_current_key())
    return genai.GenerativeModel(
        model_name=model_name,
        generation_config={"temperature": TEMPERATURE, "top_p": 0.95, "max_output_tokens": 8192},
        system_instruction=FULL_PROMPT
    )

# --- 5. INTERFEJS CZATU ---
st.title(f"🤖 Szturchacz ({wybrany_operator})")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Autostart
if len(st.session_state.messages) == 0:
    try:
        with st.spinner("Inicjalizacja systemu..."):
            m = create_model(MODEL_PRO)
            chat_init = m.start_chat(history=[])
            response_init = chat_init.send_message("start")
            st.session_state.messages.append({"role": "model", "content": response_init.text})
    except Exception as e:
        st.error(f"Błąd startu: {e}")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- 6. GŁÓWNA PĘTLA (ROTACJA + DINO FALLBACK) ---
if prompt := st.chat_input("Wklej wsad..."):
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

            # --- LOGIKA RETRY (ROTACJA KLUCZY PRO) ---
            max_retries = len(API_KEYS)
            attempts = 0
            success = False
            response_text = ""

            # Wybieramy model (jeśli już jesteśmy w trybie dinozaura, omijamy Pro)
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
                            # WYCZERPANO KLUCZE PRO -> TRYB DINOZAURA
                            if not st.session_state.is_fallback:
                                st.session_state.is_fallback = True
                                target_model = MODEL_FLASH
                                attempts = 0 # Resetujemy próby dla modelu Flash
                                placeholder.error("⚠️ Przechodzę w tryb awaryjny (Dinozaur)! Sekunda...")
                                time.sleep(2)
                                st.rerun() 
                    else:
                        st.error(f"Krytyczny błąd: {e}")
                        break
            
            if success:
                placeholder.markdown(response_text)
                st.session_state.messages.append({"role": "model", "content": response_text})
