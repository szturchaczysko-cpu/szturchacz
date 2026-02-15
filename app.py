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

# ==========================================
# 🔑 LOGOWANIE
# ==========================================
if "operator" not in st.session_state: st.session_state.operator = cookies.get("op_name", "")
if "password_correct" not in st.session_state: st.session_state.password_correct = cookies.get("auth", "") == "ok"

if not st.session_state.password_correct:
    st.header("🔑 Zaloguj się")
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

# ==========================================
# 🚀 ROUTING — ŁADOWANIE APLIKACJI VERTEX
# ==========================================
# Po zalogowaniu zawsze ładujemy app_vertex.py
# Prompt jest wybierany przez admina i zapisany w bazie (pole prompt_url)
try:
    with open("app_vertex.py", encoding="utf-8") as f:
        code = f.read()
        exec(code, globals())
except FileNotFoundError:
    st.error("Błąd: Nie znaleziono pliku app_vertex.py!")
    st.stop()
except Exception as e:
    st.error(f"Błąd ładowania aplikacji: {e}")
    st.stop()
