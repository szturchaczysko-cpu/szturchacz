import streamlit as st
import google.generativeai as genai
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

st.set_page_config(page_title="Szturchacz AI (Multi-Key)", layout="wide")

# ==========================================
# 🔒 BRAMKA BEZPIECZEŃSTWA
# ==========================================
def check_password():
    if st.session_state.get("password_correct", False):
        return True

    st.header("🔒 Dostęp chroniony")
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
# 🔑 MENEDŻER KLUCZY (ROTATOR)
# ==========================================

# 1. Pobierz listę kluczy z secrets
try:
    API_KEYS = st.secrets["API_KEYS"]
    if not isinstance(API_KEYS, list):
        # Zabezpieczenie, gdyby ktoś wpisał jeden klucz jako string
        API_KEYS = [API_KEYS]
except Exception:
    st.error("🚨 Błąd: Brak 'API_KEYS' w secrets.toml (musi to być lista!)")
    st.stop()

# 2. Ustaw indeks klucza w sesji, jeśli go nie ma
if "key_index" not in st.session_state:
    st.session_state.key_index = 0

def get_current_key():
    """Zwraca aktualnie używany klucz."""
    return API_KEYS[st.session_state.key_index]

def rotate_key():
    """Przełącza na następny klucz w liście."""
    st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)
    new_key = get_current_key()
    # Re-konfiguracja biblioteki nowym kluczem
    genai.configure(api_key=new_key)
    return st.session_state.key_index

# Konfiguracja wstępna (przy starcie)
genai.configure(api_key=get_current_key())


# ==========================================
# 🚀 APLIKACJA
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
    st.caption(f"🔑 Aktywny klucz: ...{get_current_key()[-4:]} (Index: {st.session_state.key_index + 1}/{len(API_KEYS)})")
    
    st.subheader("👤 Operator")
    wybrany_operator = st.selectbox("Kto obsługuje?", DOSTEPNI_OPERATORZY, index=0)

    st.subheader("📥 Tryb Wsadu")
    wybrany_tryb_label = st.selectbox("Skąd pochodzi wsad?", list(TRYBY_WSADU.keys()), index=0)
    wybrany_tryb_kod = TRYBY_WSADU[wybrany_tryb_label]
    
    st.markdown("---")
    if st.button("🗑️ Resetuj rozmowę"):
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

generation_config = {
    "temperature": TEMPERATURE,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 8192,
}

# --- 4. SKLEJANIE PROMPTA ---
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

# --- 5. INICJALIZACJA MODELU ---
# Funkcja pomocnicza do tworzenia modelu (potrzebna przy restarcie klucza)
def create_model():
    return genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=generation_config,
        system_instruction=FULL_PROMPT
    )

model = create_model()

# --- 6. INTERFEJS CZATU ---
st.title(f"🤖 Szturchacz ({wybrany_operator})")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Auto-start (z obsługą błędów)
if len(st.session_state.messages) == 0:
    try:
        with st.spinner("Inicjalizacja systemu..."):
            # Tutaj też może wystąpić 429, więc warto zabezpieczyć
            chat_init = model.start_chat(history=[])
            response_init = chat_init.send_message("start")
            st.session_state.messages.append({"role": "model", "content": response_init.text})
    except Exception as e:
        # Prosta obsługa błędu przy starcie - użytkownik może odświeżyć
        st.error(f"Błąd startu (spróbuj odświeżyć): {e}")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- 7. GŁÓWNA PĘTLA Z ROTACJĄ KLUCZY ---
if prompt := st.chat_input("Wklej wsad..."):
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("model"):
        placeholder = st.empty()
        with st.spinner("Analizuję..."):
            
            # Przygotowanie historii
            history_for_api = [{"role": "user", "parts": ["start"]}]
            for m in st.session_state.messages[:-1]: # Bez ostatniej wiadomości usera (bo idzie w send_message)
                history_for_api.append({"role": m["role"], "parts": [m["content"]]})
            
            # --- LOGIKA RETRY (ROTACJA) ---
            max_retries = len(API_KEYS) # Próbujemy tyle razy, ile mamy kluczy
            attempts = 0
            success = False
            response_text = ""

            while attempts < max_retries and not success:
                try:
                    # 1. Upewnij się, że konfiguracja ma aktualny klucz
                    genai.configure(api_key=get_current_key())
                    
                    # 2. Stwórz czat na nowo (żeby zassał nowy klucz)
                    # Uwaga: model musi być odświeżony, jeśli genai.configure jest globalne
                    current_model = create_model()
                    chat = current_model.start_chat(history=history_for_api)
                    
                    # 3. Wyślij wiadomość
                    response = chat.send_message(prompt)
                    response_text = response.text
                    success = True
                
                except Exception as e:
                    error_msg = str(e)
                    # Sprawdzamy czy to błąd limitu (429 lub Quota exceeded)
                    if "429" in error_msg or "Quota exceeded" in error_msg or "Resource has been exhausted" in error_msg:
                        attempts += 1
                        old_key_id = st.session_state.key_index + 1
                        new_index = rotate_key() # Zmieniamy klucz w sesji
                        
                        # Informacja dla usera (opcjonalna, można usunąć żeby było seamless)
                        placeholder.warning(f"⚠️ Limit klucza nr {old_key_id} wyczerpany. Przełączam na klucz nr {new_index + 1} i ponawiam...")
                        time.sleep(1) # Krótka pauza dla stabilności
                    else:
                        # Inny błąd (np. serwer padł, zły prompt) - przerywamy
                        st.error(f"Wystąpił krytyczny błąd API: {e}")
                        break
            
            if success:
                placeholder.markdown(response_text)
                st.session_state.messages.append({"role": "model", "content": response_text})
            elif attempts >= max_retries:
                st.error("❌ Wszystkie klucze API zostały wyczerpane! Spróbuj później lub dodaj więcej kluczy.")
