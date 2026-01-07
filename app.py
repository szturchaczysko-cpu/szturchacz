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
        try:
            if password_input == st.secrets["APP_PASSWORD"]:
                st.session_state["password_correct"] = True
                st.rerun()
            else:
                st.error("😕 Błędne hasło")
        except FileNotFoundError:
            st.error("Brak pliku secrets.toml!")
    return False

if not check_password():
    st.stop()

# ==========================================
# 🔑 MENEDŻER KLUCZY (ROTATOR - NAPRAWIONY)
# ==========================================
try:
    API_KEYS = st.secrets["API_KEYS"]
    if not isinstance(API_KEYS, list):
        API_KEYS = [API_KEYS]
except Exception:
    st.error("🚨 Błąd: Brak 'API_KEYS' w secrets.toml")
    st.stop()

# Inicjalizacja indeksu klucza (tylko raz)
if "key_index" not in st.session_state:
    st.session_state.key_index = 0

def get_current_key():
    """Pobiera klucz na podstawie aktualnego indeksu w sesji."""
    return API_KEYS[st.session_state.key_index]

def rotate_key():
    """Przesuwa indeks na następny i zwraca nowy indeks."""
    st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)
    return st.session_state.key_index

# --- KLUCZOWE: KONFIGURACJA NA STARCIE SKRYPTU ---
# To gwarantuje, że po odświeżeniu/resecie używamy ostatniego dobrego klucza
genai.configure(api_key=get_current_key())

# ==========================================
# 🚀 APLIKACJA SZTURCHACZ
# ==========================================

# --- KONFIGURACJA MODELU ---
MODEL_NAME = "gemini-3-pro-preview" 
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
    st.title("⚙️ Panel Sterowania")
    
    # Info o modelu i kluczu (dla pewności, że się zmienił)
    st.caption(f"🧠 Model: `{MODEL_NAME}`")
    st.caption(f"🌡️ Temp: `{TEMPERATURE}`")
    # Pokazujemy końcówkę klucza, żebyś widział czy się zmienił po błędzie
    current_k = get_current_key()
    st.caption(f"🔑 Klucz: ...{current_k[-4:]} (Index: {st.session_state.key_index + 1}/{len(API_KEYS)})")
    
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
        # Czyścimy tylko wiadomości, NIE czyścimy key_index!
        st.session_state.messages = []
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

# --- 3. PROMPT (Z SECRETS) ---
try:
    SYSTEM_INSTRUCTION_BASE = st.secrets["SYSTEM_PROMPT"]
except Exception:
    st.error("🚨 Brak SYSTEM_PROMPT w secrets!")
    st.stop()


    
    with st.expander("🕵️ PODGLĄD PROMPTA (Tylko dla admina)"):
        st.text(SYSTEM_INSTRUCTION_BASE)
    # ----------------------------------------

generation_config = {
    "temperature": TEMPERATURE,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 8192,
}

SECTION_14_OVERRIDE = """
*** AKTUALIZACJA LOGIKI STARTOWEJ (NADPISUJE SEKCJĘ 14) ***
14. START (ZMODYFIKOWANA LOGIKA TRYBÓW) (🟥)
Gdy instancja jest uruchamiana bez WSADU sprawy (komenda „start”):
1. Sprawdź parametr `domyslny_tryb`.
2. Przywitaj `domyslny_operator`.
3. Poproś o WSAD STARTOWY zależnie od trybu.
4. Nie stosujesz formatu 0.4 i nie uruchamiasz analizy. Czekasz na wsad.
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
def create_model():
    # Model zawsze pobierze aktualną konfigurację z genai.configure()
    return genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=generation_config,
        system_instruction=FULL_PROMPT
    )

# --- 5. INTERFEJS CZATU ---
st.title(f"🤖 Szturchacz ({wybrany_operator})")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Auto-start
if len(st.session_state.messages) == 0:
    try:
        with st.spinner("Inicjalizacja systemu..."):
            # Tutaj też używamy pętli retry, bo start też może dostać 429!
            model = create_model()
            chat_init = model.start_chat(history=[])
            response_init = chat_init.send_message("start")
            st.session_state.messages.append({"role": "model", "content": response_init.text})
    except Exception as e:
        # Jeśli start padnie, to trudno - user odświeży, ale zazwyczaj start jest lekki
        st.error(f"Błąd startu: {e}")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- 6. GŁÓWNA PĘTLA (PANCERNA ROTACJA) ---
if prompt := st.chat_input("Wklej wsad..."):
    
    with st.chat_message("user"):
        st.markdown(prompt)
        if uploaded_file:
            image_data = Image.open(uploaded_file)
            st.image(image_data, width=300)
            
    st.session_state.messages.append({"role": "user", "content": prompt})
    if uploaded_file:
        st.session_state.messages.append({"role": "user", "content": "[Załączono zdjęcie]"})

    with st.chat_message("model"):
        placeholder = st.empty()
        with st.spinner("Analizuję..."):
            
            history_for_api = [{"role": "user", "parts": ["start"]}]
            for m in st.session_state.messages[:-1]: 
                if m["content"] != "[Załączono zdjęcie]":
                    history_for_api.append({"role": m["role"], "parts": [m["content"]]})
            
            content_to_send = prompt
            if uploaded_file:
                image_data = Image.open(uploaded_file)
                content_to_send = [prompt, image_data]

            # --- LOGIKA RETRY ---
            max_retries = len(API_KEYS)
            attempts = 0
            success = False
            response_text = ""

            while attempts < max_retries and not success:
                try:
                    # 1. WYMUSZENIE KONFIGURACJI (Kluczowe dla pętli!)
                    current_key = get_current_key()
                    genai.configure(api_key=current_key)
                    
                    # 2. NOWY MODEL I CZAT (Kluczowe dla odświeżenia!)
                    current_model = create_model()
                    chat = current_model.start_chat(history=history_for_api)
                    
                    # 3. PRÓBA WYSŁANIA
                    response = chat.send_message(content_to_send)
                    response_text = response.text
                    success = True
                
                except Exception as e:
                    # Wykrywanie błędu limitu
                    is_quota_error = isinstance(e, google_exceptions.ResourceExhausted) or \
                                     "429" in str(e) or \
                                     "Quota exceeded" in str(e) or \
                                     "403" in str(e)

                    if is_quota_error:
                        attempts += 1
                        old_key_index = st.session_state.key_index
                        
                        # ZMIANA KLUCZA W SESJI (TRWAŁA)
                        rotate_key()
                        
                        placeholder.warning(f"⚠️ Klucz nr {old_key_index + 1} wyczerpany. Przełączam na klucz nr {st.session_state.key_index + 1} i ponawiam...")
                        time.sleep(1) # Oddech dla API
                    else:
                        st.error(f"Krytyczny błąd API: {e}")
                        break
            
            if success:
                placeholder.markdown(response_text)
                st.session_state.messages.append({"role": "model", "content": response_text})
            elif attempts >= max_retries:
                st.error("❌ Wszystkie klucze API wyczerpane! Spróbuj później.")
