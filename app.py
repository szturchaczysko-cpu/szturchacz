import streamlit as st
import google.generativeai as genai
from google.generativeai import caching
from google.api_core import exceptions as google_exceptions
from datetime import datetime, timedelta
import locale, time, json, re, pytz, hashlib, random
import firebase_admin
from firebase_admin import credentials, firestore
from streamlit_cookies_manager import EncryptedCookieManager

# --- 0. KONFIGURACJA ---
st.set_page_config(page_title="Szturchacz AI", layout="wide")
try: locale.setlocale(locale.LC_TIME, "pl_PL.UTF-8")
except: pass

if not firebase_admin._apps:
    creds_dict = json.loads(st.secrets["FIREBASE_CREDS"])
    creds = credentials.Certificate(creds_dict)
    firebase_admin.initialize_app(creds)
db = firestore.client()

cookies = EncryptedCookieManager(password=st.secrets.get("COOKIE_PASSWORD", "dev_pass"))
if not cookies.ready(): st.stop()

# --- FUNKCJE STATYSTYK ---
def parse_pz(text):
    if not text: return None
    match = re.search(r'COP#\s*PZ\s*:\s*(PZ\d+)', text, re.IGNORECASE)
    if match: return match.group(1).upper()
    return None


def log_stats(op_name, start_pz, end_pz, key_idx):
    tz_pl = pytz.timezone('Europe/Warsaw')
    now_pl = datetime.now(tz_pl)
    today = now_pl.strftime("%Y-%m-%d")
    time_str = now_pl.strftime("%H:%M") # Pobieramy godzinę i minutę
    
    doc_ref = db.collection("stats").document(today).collection("operators").document(op_name)
    
    upd = {
        "sessions_completed": firestore.Increment(1),
        # NOWOŚĆ: Dodajemy godzinę sesji do listy w bazie
        "session_times": firestore.ArrayUnion([time_str]) 
    }
    
    if start_pz and end_pz:
        upd[f"pz_transitions.{start_pz}_to_{end_pz}"] = firestore.Increment(1)
        if end_pz == "PZ6":
            db.collection("global_stats").document("totals").collection("operators").document(op_name).set({"total_diamonds": firestore.Increment(1)}, merge=True)
            
    doc_ref.set(upd, merge=True)
    db.collection("key_usage").document(today).set({str(key_idx + 1): firestore.Increment(1)}, merge=True)





# ==========================================
# 🔒 LOGOWANIE I ROUTING
# ==========================================
if "operator" not in st.session_state: st.session_state.operator = cookies.get("op_name", "")
if "password_correct" not in st.session_state: st.session_state.password_correct = cookies.get("auth", "") == "ok"

if not st.session_state.password_correct:
    st.header("🔒 Zaloguj się")
    input_pwd = st.text_input("Wpisz swoje hasło:", type="password")
    if st.button("Wejdź"):
        query = db.collection("operator_configs").where("password", "==", input_pwd).limit(1).get()
        if query:
            op_doc = query[0]
            st.session_state.operator = op_doc.id
            st.session_state.password_correct = True
            cookies["op_name"] = op_doc.id
            cookies["auth"] = "ok"
            cookies.save()
            st.rerun()
        else:
            st.error("Błędne hasło!")
    st.stop()

# --- POBIERANIE CONFIGU ---
op_name = st.session_state.operator
cfg_ref = db.collection("operator_configs").document(op_name)
cfg = cfg_ref.get().to_dict() or {}

target_file = cfg.get("app_file", "app.py")
if target_file != "app.py":
    try:
        with open(target_file, encoding="utf-8") as f:
            code = f.read()
            exec(code, globals())
            st.stop()
    except FileNotFoundError:
        st.error(f"Błąd: Nie znaleziono pliku {target_file}!")
        st.stop()

# ==========================================
# 🚀 LOGIKA SZTURCHACZA
# ==========================================
global_cfg = db.collection("admin_config").document("global_settings").get().to_dict() or {}
show_diamonds_globally = global_cfg.get("show_diamonds", True)

tz_pl = pytz.timezone('Europe/Warsaw')
today_s = datetime.now(tz_pl).strftime("%Y-%m-%d")
today_data = db.collection("stats").document(today_s).collection("operators").document(op_name).get().to_dict() or {}
today_diamonds = sum(v for k, v in today_data.get("pz_transitions", {}).items() if k.endswith("_to_PZ6"))
global_data = db.collection("global_stats").document("totals").collection("operators").document(op_name).get().to_dict() or {}
all_time_diamonds = global_data.get("total_diamonds", 0)

API_KEYS = st.secrets["API_KEYS"]
MODEL_MAP = {
    "Gemini 1.5 Pro (2.5) - Zalecany": "gemini-2.5-pro",
    "Gemini 3.0 Pro - Chirurgiczny": "gemini-3-pro-preview"
}
TEMPERATURE = 0.0

# --- LOGIKA PRZYPISANIA KLUCZA ---
fixed_key_idx = cfg.get("assigned_key_index", 0)
if fixed_key_idx > 0:
    st.session_state.key_index = fixed_key_idx - 1
    is_key_locked = True
else:
    is_key_locked = False
    if "key_index" not in st.session_state:
        st.session_state.key_index = random.randint(0, len(API_KEYS) - 1)

if "messages" not in st.session_state: st.session_state.messages = []
if "chat_started" not in st.session_state: st.session_state.chat_started = False
if "current_start_pz" not in st.session_state: st.session_state.current_start_pz = None

def get_current_key(): return API_KEYS[st.session_state.key_index]
def rotate_key():
    if not is_key_locked:
        st.session_state.key_index = (st.session_state.key_index + 1) % len(API_KEYS)
    return st.session_state.key_index

# --- SIDEBAR ---
with st.sidebar:
    st.title(f"👤 {op_name}")
    if show_diamonds_globally:
        st.markdown(f"### 💎 Zamówieni kurierzy\n**Dziś:** {today_diamonds} | **Łącznie:** {all_time_diamonds}")
        st.markdown("---")

    admin_msg = cfg.get("admin_message", "")
    msg_read = cfg.get("message_read", False)
    if admin_msg:
        if not msg_read:
            st.error(f"📢 **WIADOMOŚĆ:**\n\n{admin_msg}")
            if st.button("✅ Odczytałem"):
                cfg_ref.update({"message_read": True})
                st.rerun()
        else:
            with st.expander("📩 Poprzednia wiadomość"): st.write(admin_msg)

    st.markdown("---")
    model_label = st.radio("Model AI:", list(MODEL_MAP.keys()), key="selected_model_label")
    active_model_id = MODEL_MAP[st.session_state.selected_model_label]
    
    st.caption(f"🧠 Model: `{active_model_id}`")
    if is_key_locked:
        st.success(f"🔒 Klucz przypisany: {st.session_state.key_index + 1}")
    else:
        st.caption(f"🔑 Klucz (Rotator): {st.session_state.key_index + 1}/{len(API_KEYS)}")

    st.markdown("---")
    TRYBY_DICT = {
        "Standard (Panel + Koperta)": "od_szturchacza",
        "WhatsApp (Rolka + Panel)": "WA",
        "E-mail (Rolka + Panel)": "MAIL",
        "Forum/Inne (Wpis + Panel)": "FORUM"
    }
    st.selectbox("Tryb Startowy:", list(TRYBY_DICT.keys()), key="tryb_label")
    wybrany_tryb_kod = TRYBY_DICT[st.session_state.tryb_label]
    
    if st.button("🚀 Nowa sprawa / Reset", type="primary"):
        st.session_state.messages = []
        st.session_state.chat_started = True
        st.session_state.current_start_pz = None
        if not is_key_locked:
            st.session_state.key_index = random.randint(0, len(API_KEYS) - 1)
        for k in list(st.session_state.keys()):
            if k.startswith("cache_"): del st.session_state[k]
        st.rerun()

    if st.button("🚪 Wyloguj"):
        st.session_state.clear()
        cookies.clear()
        cookies.save()
        st.rerun()

# --- GŁÓWNY INTERFEJS ---
st.title(f"🤖 Szturchacz")

if not st.session_state.chat_started:
    st.info("👈 Skonfiguruj panel i kliknij 'Nowa sprawa / Reset'.")
else:
    SYSTEM_PROMPT = st.secrets["SYSTEM_PROMPT"]
    parametry_startowe = f"\ndomyslny_operator={op_name}\ndomyslna_data={datetime.now(tz_pl).strftime('%d.%m')}\nGrupa_Operatorska={cfg.get('role', 'Operatorzy_DE')}\ndomyslny_tryb={wybrany_tryb_kod}"
    FULL_PROMPT = SYSTEM_PROMPT + parametry_startowe

    def get_or_create_model(model_name, full_prompt):
        prompt_hash = hashlib.md5(full_prompt.encode()).hexdigest()
        cache_key = f"cache_{st.session_state.key_index}_{model_name}_{prompt_hash}"
        if st.session_state.get(cache_key):
            try: return genai.GenerativeModel.from_cached_content(st.session_state[cache_key])
            except: del st.session_state[cache_key]
        genai.configure(api_key=get_current_key())
        if "gemini-1.5-pro" in model_name:
            with st.spinner(f"Tworzenie cache dla klucza {st.session_state.key_index + 1}..."):
                cache = caching.CachedContent.create(model=f'models/{model_name}', system_instruction=full_prompt, ttl=timedelta(hours=1))
                st.session_state[cache_key] = cache
                return genai.GenerativeModel.from_cached_content(cache)
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
                if not is_key_locked and (isinstance(e, google_exceptions.ResourceExhausted) or "429" in str(e) or "Quota" in str(e) or "403" in str(e)):
                    attempts += 1
                    rotate_key()
                    st.toast(f"🔄 Rotacja: Klucz {st.session_state.key_index + 1}")
                    time.sleep(1)
                else: return f"Błąd API: {str(e)}", False
        return "❌ Wszystkie klucze wyczerpane.", False

    if len(st.session_state.messages) == 0:
        st.subheader(f"📥 Pierwszy wsad ({op_name})")
        
        # --- DYNAMICZNE INSTRUKCJE WSADU ---
        if wybrany_tryb_kod == "od_szturchacza":
            st.info("💡 **Tryb Standard:** Wklej tylko **Tabelkę z panelu** oraz **Kopertę**. O rolkę rozmowy poproszę później, jeśli będzie potrzebna.")
        else:
            st.warning(f"💡 **Tryb {st.session_state.tryb_label}:** Wklej **Tabelkę**, **Kopertę** oraz **Rolkę rozmowy**.")
            st.markdown(f"Użyj poniższego nagłówka przed wklejeniem treści rozmowy:")
            st.code(f"ROLKA_START_{wybrany_tryb_kod}")

        wsad_input = st.text_area("Wklej dane tutaj:", height=350)
        if st.button("🚀 Rozpocznij analizę", type="primary"):
            if wsad_input:
                st.session_state.current_start_pz = parse_pz(wsad_input) or "PZ_START"
                st.session_state.messages.append({"role": "user", "content": wsad_input})
                with st.spinner("Analiza..."):
                    res_text, success = call_gemini_with_rotation([], wsad_input)
                    if success:
                        st.session_state.messages.append({"role": "model", "content": res_text})
                        log_stats(op_name, st.session_state.current_start_pz, parse_pz(res_text) or "PZ_END", st.session_state.key_index)
                        st.rerun()
                    else: st.error(res_text)
            else: st.error("Wsad jest pusty!")
    else:
        st.subheader(f"💬 Rozmowa: {op_name}")
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if prompt := st.chat_input("Odpowiedz AI..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("model"):
                with st.spinner("Analizuję..."):
                    history_api = [{"role": m["role"], "parts": [m["content"]]} for m in st.session_state.messages[:-1]]
                    res_text, success = call_gemini_with_rotation(history_api, prompt)
                    if success:
                        st.markdown(res_text)
                        st.session_state.messages.append({"role": "model", "content": res_text})
                        if 'cop#' in res_text.lower() and 'c#' in res_text.lower():
                            log_stats(op_name, st.session_state.current_start_pz, parse_pz(res_text) or "PZ_END", st.session_state.key_index)
                    else: st.error(res_text)
