from datetime import datetime, timedelta
import csv
import time
import os
import glob
import base64
import subprocess
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
import pandas as pd
from fastapi import FastAPI, Form, Request, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
import uvicorn

# ==================== CHEMINS ====================
# Détection de l'environnement (local ou Back4app)
if os.environ.get("BACK4APP_APP_ID"):
    # Mode production sur Back4app
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    MODE_BACK4APP = True
else:
    # Mode développement local
    BASE_DIR = "/home/daniel/robot_vols"
    MODE_BACK4APP = False

DOSSIER_PDF = os.path.join(BASE_DIR, "xpdfs")
CSV_AEROPORTS_PATH = os.path.join(BASE_DIR, "tous_les_aeroports_mondiaux.csv")
HISTORIQUE_CSV = os.path.join(BASE_DIR, "historique_vols.csv")
DOSSIER_ENREGISTREMENT = os.path.join(BASE_DIR, "enregistrement_ip")
FICHIER_VISITES = os.path.join(DOSSIER_ENREGISTREMENT, "visites.txt")
DOSSIER_CERTS = os.path.join(BASE_DIR, "certs")
CERT_FILE = os.path.join(DOSSIER_CERTS, "cert.pem")
KEY_FILE = os.path.join(DOSSIER_CERTS, "key.pem")

# Créer les dossiers (sur Back4app, on les crée dans le répertoire de l'app)
os.makedirs(DOSSIER_ENREGISTREMENT, exist_ok=True)
os.makedirs(DOSSIER_PDF, exist_ok=True)
if not MODE_BACK4APP:
    os.makedirs(DOSSIER_CERTS, exist_ok=True)

# ==================== ÉTAT GLOBAL ====================
recherche_en_cours = False
verrou_recherche = threading.Lock()
mode_livraison = "telecharger"
email_destinataire = ""

# ==================== GÉNÉRATION DU CERTIFICAT SSL ====================
def generer_certificat():
    """Génère un certificat SSL auto-signé si inexistant."""
    if not MODE_BACK4APP and (not os.path.exists(CERT_FILE) or not os.path.exists(KEY_FILE)):
        print("🔐 Génération du certificat SSL auto-signé...")
        try:
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:4096",
                "-nodes", "-keyout", KEY_FILE, "-out", CERT_FILE,
                "-days", "365", "-subj", "/CN=localhost"
            ], check=True, capture_output=True)
            print("✅ Certificat SSL généré avec succès")
        except Exception as e:
            print(f"⚠️ Erreur génération certificat: {e}")

# Générer le certificat uniquement en local
if not MODE_BACK4APP:
    generer_certificat()

app = FastAPI()

# ==================== CONFIGURATION ====================
MOT_DE_PASSE = os.environ.get("MOT_DE_PASSE", "David56380")
EMAIL_EXPEDITEUR = os.environ.get("EMAIL_EXPEDITEUR", "bouvarddaniel@gmail.com")
MOT_DE_PASSE_EMAIL = os.environ.get("MOT_DE_PASSE_EMAIL", "kgkr sjgp kphp fuos")
SERVEUR_SMTP = "smtp.gmail.com"
PORT_SMTP = 465

# ==================== FONCTIONS ====================
def enregistrer_visite(ip: str, tentative: str = "", statut: str = "connexion", villes: str = ""):
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(FICHIER_VISITES, "a", encoding="utf-8") as f:
            if statut == "echec":
                f.write(f"{now} | IP: {ip} | Tentative: {tentative}\n")
            elif statut == "succes":
                f.write(f"{now} | IP: {ip} | Connexion réussie\n")
            elif statut == "recherche" and villes:
                f.write(f"{now} | IP: {ip} | Recherche: {villes}\n")
    except Exception as e:
        print(f"⚠️ Erreur enregistrement visite : {e}")

def obtenir_ip_visiteur(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"

def obtenir_code_iata(nom_ville: str) -> str:
    if not nom_ville:
        return ""
    
    if "(" in nom_ville and ")" in nom_ville:
        possible_iata = nom_ville.split("(")[-1].split(")")[0].strip()
        if len(possible_iata) == 3:
            return possible_iata.upper()

    if not os.path.exists(CSV_AEROPORTS_PATH):
        return nom_ville.upper()
    
    colonnes = ["ID", "Nom", "Ville", "Pays", "IATA", "OACI", "Latitude", "Longitude", "Altitude", "Fuseau", "DST", "TZ", "Type", "Source"]
    try:
        df = pd.read_csv(CSV_AEROPORTS_PATH, header=None, names=colonnes, dtype=str, on_bad_lines="skip")
        if len(df) > 0 and df.iloc[0]["Ville"] == "Ville":
            df = df.iloc[1:]
        
        df["Ville"] = df["Ville"].fillna("").str.replace('"', '', regex=False).str.strip()
        df["IATA"] = df["IATA"].fillna("").str.replace('"', '', regex=False).str.strip()
        
        match = df[(df["Ville"].str.lower() == nom_ville.strip().lower()) | (df["IATA"].str.lower() == nom_ville.strip().lower())]
        if not match.empty:
            iata = match.iloc[0]["IATA"]
            if pd.notna(iata) and len(iata) == 3:
                return iata
    except Exception as e:
        print(f"⚠️ Erreur lors de la lecture du CSV aéroports : {e}")
        
    return nom_ville.upper()

def envoyer_email(destinataire, fichier_pdf):
    try:
        if not os.path.exists(fichier_pdf):
            print(f"❌ Fichier {fichier_pdf} inexistant")
            return False
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL_EXPEDITEUR
        msg['To'] = destinataire
        msg['Subject'] = "📊 Rapport de recherche de vols"
        
        corps = f"""
Bonjour,

Veuillez trouver ci-joint le rapport de recherche de vols généré par le Robot Kayak.

Date de génération : {datetime.now().strftime("%d/%m/%Y %H:%M")}

Cordialement,
L'équipe Robot Vols
"""
        msg.attach(MIMEText(corps, 'utf-8'))
        
        with open(fichier_pdf, "rb") as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename=manuel_vols.pdf')
            msg.attach(part)
        
        with smtplib.SMTP_SSL(SERVEUR_SMTP, PORT_SMTP) as smtp:
            smtp.login(EMAIL_EXPEDITEUR, MOT_DE_PASSE_EMAIL)
            smtp.sendmail(EMAIL_EXPEDITEUR, destinataire, msg.as_string())
        
        return True
    except Exception as e:
        print(f"❌ Erreur envoi email : {e}")
        return False

# ==================== TEMPLATES HTML ====================
# [LES TEMPLATES get_html_login, get_html_attente, get_html_choix, get_html_form, get_html_suivi]
# Restent identiques à votre version actuelle. Je les ai tronqués pour la lisibilité.
# Copiez-les depuis votre fichier existant.

def get_html_login():
    return """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Connexion - Robot Vols</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .login-box { background: white; padding: 48px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); width: 100%; max-width: 400px; }
        .login-box .logo { text-align: center; font-size: 48px; margin-bottom: 8px; }
        .login-box h2 { color: #1a1a2e; text-align: center; margin-bottom: 8px; font-size: 28px; font-weight: 700; }
        .login-box .subtitle { text-align: center; color: #888; font-size: 14px; margin-bottom: 32px; }
        .login-box label { font-weight: 600; display: block; margin-top: 20px; color: #333; font-size: 14px; }
        .login-box input[type="password"] { width: 100%; padding: 14px; margin-top: 8px; border: 2px solid #e8ecf1; border-radius: 12px; box-sizing: border-box; font-size: 16px; transition: border-color 0.3s, box-shadow 0.3s; background: #f8f9fa; }
        .login-box input[type="password"]:focus { border-color: #ff690f; outline: none; box-shadow: 0 0 0 4px rgba(255, 105, 15, 0.1); background: white; }
        .login-box button { background: linear-gradient(135deg, #ff690f, #ff8c42); color: white; padding: 16px; border: none; border-radius: 12px; cursor: pointer; width: 100%; font-size: 18px; font-weight: 600; margin-top: 28px; transition: transform 0.2s, box-shadow 0.2s; position: relative; z-index: 999; }
        .login-box button:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(255, 105, 15, 0.3); }
        .error { color: #d93025; font-size: 14px; text-align: center; margin-top: 12px; background: #fde8e8; padding: 12px; border-radius: 8px; }
        .security-note { text-align: center; color: #999; font-size: 12px; margin-top: 16px; }
    </style>
</head>
<body>
    <div class="login-box">
        <div class="logo">✈️</div>
        <h2>Saint Raoul c'est cool!</h2>
        <p class="subtitle">Recherche automatique de vols</p>
        <form method="post" action="/login">
            <label for="password">🔑 Mot de passe</label>
            <input type="password" id="password" name="password" required autofocus placeholder="Entrez votre mot de passe">
            <button type="submit">Se connecter</button>
            {ERREUR}
        </form>
        <p class="security-note">🔒 Connexion sécurisée via HTTPS</p>
    </div>
</body>
</html>"""

def get_html_attente():
    return """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Recherche en cours - Robot Vols</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container { background: white; padding: 48px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); width: 100%; max-width: 500px; text-align: center; }
        .logo { font-size: 48px; margin-bottom: 16px; }
        h2 { color: #1a1a2e; margin-bottom: 12px; }
        .message { color: #555; font-size: 16px; line-height: 1.6; margin: 16px 0; background: #fff3cd; padding: 16px; border-radius: 12px; border: 1px solid #ffc107; }
        .btn { display: inline-block; background: #6c757d; color: white; padding: 12px 32px; text-decoration: none; border-radius: 10px; font-weight: 500; margin-top: 12px; transition: background 0.3s; }
        .btn:hover { background: #5a6268; }
        .security-note { color: #999; font-size: 12px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">⏳</div>
        <h2>Recherche en cours</h2>
        <div class="message">
            🔄 Une recherche est déjà en cours sur le robot.<br>
            Merci de patienter ou de revenir dans quelques instants.
        </div>
        <a href="/" class="btn">⬅️ Retour à l'accueil</a>
        <p class="security-note">🔒 Connexion sécurisée via HTTPS</p>
    </div>
</body>
</html>"""

# [get_html_choix, get_html_form, get_html_suivi sont identiques à votre version actuelle]
# Copiez-les depuis votre fichier mon_api.py existant

# ==================== ROUTES ====================

@app.get("/etat_recherche")
def etat_recherche():
    return {"en_cours": recherche_en_cours}

@app.get("/villes")
def api_villes(q: str, request: Request):
    if not os.path.exists(CSV_AEROPORTS_PATH):
        return []
    colonnes = ["ID", "Nom", "Ville", "Pays", "IATA", "OACI", "Latitude", "Longitude", "Altitude", "Fuseau", "DST", "TZ", "Type", "Source"]
    try:
        df = pd.read_csv(CSV_AEROPORTS_PATH, header=None, names=colonnes, dtype=str, on_bad_lines="skip")
        if len(df) > 0 and df.iloc[0]["Ville"] == "Ville":
            df = df.iloc[1:]
        
        df["Ville"] = df["Ville"].fillna("").str.replace('"', '', regex=False).str.strip()
        df["Nom"] = df["Nom"].fillna("").str.replace('"', '', regex=False).str.strip()
        df["IATA"] = df["IATA"].fillna("").str.replace('"', '', regex=False).str.strip()
        
        q_lower = q.lower().strip()
        if not q_lower:
            return []

        match = df[
            df["Ville"].str.lower().str.contains(q_lower, na=False) | 
            df["IATA"].str.lower().str.contains(q_lower, na=False) |
            df["Nom"].str.lower().str.contains(q_lower, na=False)
        ]
        
        match = match[match["IATA"].str.len() == 3]
        
        if "paris" in q_lower:
            cdg_ory = match[match["IATA"].isin(["CDG", "ORY"])]
            autres = match[~match["IATA"].isin(["CDG", "ORY"])]
            autres = autres[autres["IATA"] != "LBG"]
            match = pd.concat([cdg_ory, autres])

        resultats = []
        for _, row in match.head(15).iterrows():
            ville = row["Ville"]
            nom = row["Nom"]
            iata = row["IATA"]
            if pd.notna(iata) and iata:
                ligne_affichage = f"{ville} - {nom} ({iata})"
                if ligne_affichage not in resultats:
                    resultats.append(ligne_affichage)
        return resultats[:15]
    except Exception as e:
        print(f"Erreur /villes : {e}")
        return []

@app.get("/choix", response_class=HTMLResponse)
def choix(request: Request):
    auth_cookie = request.cookies.get("auth_cookie")
    if auth_cookie != "valide":
        return RedirectResponse(url="/", status_code=303)
    
    rapports_html = ""
    if os.path.exists(DOSSIER_PDF):
        fichiers = [f for f in os.listdir(DOSSIER_PDF) if f.endswith('.pdf') and f != 'manuel_vols.pdf']
        if fichiers:
            rapports_html = ""
            for f in fichiers[:5]:
                chemin = os.path.join(DOSSIER_PDF, f)
                taille = os.path.getsize(chemin) // 1024
                rapports_html += f"<div class='file-item'><span>{f}</span><span>{taille} KB</span></div>"
        else:
            rapports_html = "<div style='text-align:center;color:#888;padding:8px 0;'>Aucun rapport existant</div>"
    
    return get_html_choix().replace("{RAPPORTS_EXISTANTS}", rapports_html)

@app.post("/valider_choix", response_class=HTMLResponse)
def valider_choix(
    request: Request,
    mode: str = Form(...),
    email: str = Form("")
):
    global mode_livraison, email_destinataire
    
    mode_livraison = mode
    email_destinataire = email if mode == "email" else ""
    
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="mode_livraison", value=mode, path="/")
    if email:
        response.set_cookie(key="email_destinataire", value=email, path="/")
    else:
        response.delete_cookie(key="email_destinataire", path="/")
    
    return response

@app.get("/", response_class=HTMLResponse)
def index(request: Request, auth_cookie: str = Cookie(None)):
    if auth_cookie != "valide":
        return get_html_login().replace("{ERREUR}", "")
    
    mode_livraison_cookie = request.cookies.get("mode_livraison")
    email_destinataire_cookie = request.cookies.get("email_destinataire", "")
    
    if not mode_livraison_cookie:
        return RedirectResponse(url="/choix", status_code=303)
    
    if mode_livraison_cookie == "email":
        mode_texte = "📧 Envoi par email"
        email_info = f"<br>📧 Adresse : <strong>{email_destinataire_cookie or 'non renseignée'}</strong>"
    else:
        mode_texte = "⬇️ Téléchargement automatique"
        email_info = ""
    
    return get_html_form().replace("{MODE_TEXTE}", mode_texte).replace("{EMAIL_INFO}", email_info)

@app.post("/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form(...)):
    ip = obtenir_ip_visiteur(request)
    
    if password != MOT_DE_PASSE:
        enregistrer_visite(ip, tentative=password, statut="echec")
        return get_html_login().replace("{ERREUR}", "<div class='error'>❌ Mot de passe incorrect</div>")
    
    enregistrer_visite(ip, statut="succes")
    response = RedirectResponse(url="/choix", status_code=303)
    response.set_cookie(key="auth_cookie", value="valide", path="/")
    return response

@app.get("/logout", response_class=HTMLResponse)
def logout(request: Request):
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="auth_cookie", path="/")
    response.delete_cookie(key="mode_livraison", path="/")
    response.delete_cookie(key="email_destinataire", path="/")
    return response

@app.get("/verifier_pdf")
def verifier_pdf():
    fichier_manuel = os.path.join(DOSSIER_PDF, "manuel_vols.pdf")
    return {"exists": os.path.exists(fichier_manuel)}

@app.get("/lancer", response_class=HTMLResponse)
def lancer(
    request: Request,
    depart: str = "",
    ville_depart: str = "",
    ville_arrivee: str = "",
    duree_sejour: int = 90,
    nb_boucles: int = 0
):
    global recherche_en_cours, mode_livraison, email_destinataire
    
    if recherche_en_cours:
        return get_html_attente()
    
    ip = obtenir_ip_visiteur(request)
    enregistrer_visite(ip, statut="recherche", villes=f"{ville_depart} -> {ville_arrivee}")
    
    mode_livraison = request.cookies.get("mode_livraison", "telecharger")
    email_destinataire = request.cookies.get("email_destinataire", "")
    
    return get_html_suivi(depart, ville_depart, ville_arrivee, duree_sejour, nb_boucles, mode_livraison, email_destinataire)

@app.get("/lancer_stream")
def lancer_stream(
    depart: str = "",
    ville_depart: str = "",
    ville_arrivee: str = "",
    duree_sejour: int = 90,
    nb_boucles: int = 0
):
    global recherche_en_cours
    
    with verrou_recherche:
        if recherche_en_cours:
            return StreamingResponse(
                generate_erreur("Une recherche est déjà en cours. Veuillez patienter."),
                media_type="text/plain; charset=utf-8"
            )
        recherche_en_cours = True
    
    def generate():
        global recherche_en_cours
        try:
            if os.path.exists(DOSSIER_PDF):
                for fichier_pdf in glob.glob(os.path.join(DOSSIER_PDF, "*.pdf")):
                    try:
                        os.remove(fichier_pdf)
                    except Exception:
                        pass

            yield "=== DÉMARRAGE DU ROBOT Saint Raoul c'est cool! ===\n"
            
            iata_dep = obtenir_code_iata(ville_depart)
            iata_arr = obtenir_code_iata(ville_arrivee)
            yield f"🔍 Résolution IATA : {ville_depart} ➔ {iata_dep} | {ville_arrivee} ➔ {iata_arr}\n"
            yield f"📅 Départ de base : {depart} | Durée : {duree_sejour} jours | Décalages : +/- {nb_boucles}\n\n"

            txt_urls_path = os.path.join(DOSSIER_PDF, "urls_kayak.txt")
            f_txt_urls = open(txt_urls_path, "w", encoding="utf-8")

            # === SUR BACK4APP : MODE DÉMONSTRATION ===
            if MODE_BACK4APP:
                yield "⚠️ Mode démonstration (Back4app) - Les recherches en direct ne sont pas disponibles.\n"
                yield "📊 Veuillez utiliser la version locale pour les recherches automatiques.\n"
                yield "🏁 Simulation terminée.\n"
                f_txt_urls.close()
                return
            
            # === MODE LOCAL AVEC SELENIUM ===
            try:
                from selenium import webdriver
                from selenium.webdriver.chrome.service import Service
                from selenium.webdriver.common.by import By
                from webdriver_manager.chrome import ChromeDriverManager
            except ImportError as e:
                yield f"❌ Erreur: {e}\n"
                yield "⚠️ Veuillez installer Selenium: pip install selenium webdriver-manager\n"
                return

            options = webdriver.ChromeOptions()
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")

            try:
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
                driver.maximize_window()
                date_depart_base_obj = datetime.strptime(depart.strip(), "%d/%m/%Y")

                offsets = [0]
                for i in range(1, nb_boucles + 1):
                    offsets.append(i)
                for i in range(1, nb_boucles + 1):
                    offsets.append(-i)

                tour_global = 0
                for d_offset in offsets:
                    current_depart_obj = date_depart_base_obj + timedelta(days=d_offset)
                    depart_str_fr = current_depart_obj.strftime("%d/%m/%Y")
                    depart_str_iso = current_depart_obj.strftime("%Y-%m-%d")
                    
                    for r_offset in offsets:
                        tour_global += 1
                        current_retour_obj = current_depart_obj + timedelta(days=duree_sejour + r_offset)
                        retour_str_fr = current_retour_obj.strftime("%d/%m/%Y")
                        retour_str_iso = current_retour_obj.strftime("%Y-%m-%d")
                        
                        yield f"✈️ Tour {tour_global} | All: {depart_str_fr} (+{d_offset}j) | Ret: {retour_str_fr} (durée {duree_sejour + r_offset}j)\n"

                        try:
                            url_kayak = f"https://www.kayak.fr/flights/{iata_dep}-{iata_arr}/{depart_str_iso}/{retour_str_iso}?ucs=1yubyqb&sort=bestflight_a"
                            driver.get(url_kayak)
                            time.sleep(6)

                            try:
                                bouton_cookies = driver.find_element(By.XPATH, "//button[contains(., 'Tout accepter') or contains(., 'Accepter tout')]")
                                bouton_cookies.click()
                                time.sleep(1)
                            except Exception:
                                pass

                            url_resultats = driver.current_url

                            f_txt_urls.write(f"{url_resultats}\n")
                            f_txt_urls.flush()

                            duree_reelle = duree_sejour + r_offset
                            donnees = [
                                datetime.now().strftime("%d/%m/%Y %H:%M"),
                                ville_depart,
                                ville_arrivee,
                                depart_str_fr,
                                retour_str_fr,
                                str(duree_reelle),
                                str(url_resultats),
                            ]
                            with open(HISTORIQUE_CSV, "a", newline="", encoding="utf-8") as f:
                                writer = csv.writer(f)
                                writer.writerow(donnees)
                                f.flush()
                            yield "   ➔ Enregistré dans le CSV et le fichier TXT.\n"

                            try:
                                time.sleep(10)
                                pdf_data = driver.print_page()
                                url_clean = url_resultats.replace('https://www.kayak.fr/', '').replace('/', '_').replace('?', '_').replace('=', '_').replace('&', '_')
                                nom_pdf = f"vol_{iata_dep}_{iata_arr}_dep_{depart_str_fr.replace('/', '-')}_ret_{retour_str_fr.replace('/', '-')}_{url_clean}.pdf"
                                chemin_pdf = os.path.join(DOSSIER_PDF, nom_pdf)
                                with open(chemin_pdf, "wb") as f_pdf:
                                    f_pdf.write(base64.b64decode(pdf_data))
                                yield "   ➔ PDF généré avec URL cliquable dans le rapport.\n"
                            except Exception as e_pdf:
                                yield f"   ⚠️ Erreur PDF : {e_pdf}\n"

                        except Exception as e_tour:
                            yield f"   ❌ Erreur sur ce tour : {e_tour}\n"

                driver.quit()
                f_txt_urls.close()
                yield "\n🏁 Toutes les recherches sont terminées !\n"
                
                # Lancer l'extraction
                script_path = os.path.join(BASE_DIR, "extraire_manuel_vols.py")
                if os.path.exists(script_path):
                    yield "📊 Extraction des données en cours...\n"
                    result = subprocess.run(
                        ["python3", script_path],
                        capture_output=True,
                        text=True,
                        timeout=300,
                        cwd=BASE_DIR
                    )
                    if result.returncode == 0:
                        fichier_manuel = os.path.join(DOSSIER_PDF, "manuel_vols.pdf")
                        if os.path.exists(fichier_manuel):
                            yield "✅ Rapport PDF généré avec succès !\n"
                        else:
                            yield "⚠️ Le script a terminé mais le PDF n'a pas été généré.\n"
                    else:
                        yield f"⚠️ Erreur extraction: {result.stderr}\n"
                else:
                    yield "⚠️ Script d'extraction non trouvé\n"
                    
            except Exception as e_selenium:
                yield f"\n❌ Erreur Selenium / Navigateur : {e_selenium}\n"
                    
        except Exception as e_globale:
            yield f"\n❌ Erreur critique : {e_globale}\n"
        finally:
            with verrou_recherche:
                recherche_en_cours = False

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")

def generate_erreur(message: str):
    yield f"❌ {message}\n"

@app.get("/telecharger_pdf")
def telecharger_pdf():
    fichier_manuel = os.path.join(DOSSIER_PDF, "manuel_vols.pdf")
    if os.path.exists(fichier_manuel):
        return FileResponse(
            fichier_manuel,
            media_type='application/pdf',
            filename='manuel_vols.pdf',
            headers={'Content-Disposition': 'attachment; filename="manuel_vols.pdf"'}
        )
    else:
        return {"status": "error", "message": "Le fichier manuel_vols.pdf n'existe pas encore"}

@app.post("/envoyer_email")
async def envoyer_email_par_api(request: Request):
    try:
        data = await request.json()
        email_dest = data.get('email')
        
        if not email_dest:
            return {"status": "error", "message": "Adresse email manquante"}
        
        fichier_manuel = os.path.join(DOSSIER_PDF, "manuel_vols.pdf")
        if not os.path.exists(fichier_manuel):
            return {"status": "error", "message": "Le rapport PDF n'a pas encore été généré"}
        
        if envoyer_email(email_dest, fichier_manuel):
            return {"status": "success", "message": f"Rapport envoyé à {email_dest}"}
        else:
            return {"status": "error", "message": "Erreur lors de l'envoi de l'email"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/extraire_pdf")
def extraire_pdf():
    try:
        script_path = os.path.join(BASE_DIR, "extraire_manuel_vols.py")
        
        if not os.path.exists(script_path):
            return {
                "status": "error",
                "message": f"Le script {script_path} n'existe pas"
            }
        
        pdfs = glob.glob(os.path.join(DOSSIER_PDF, "*.pdf"))
        if not pdfs:
            return {
                "status": "error",
                "message": "Aucun PDF trouvé à extraire"
            }
        
        print(f"📄 {len(pdfs)} PDFs trouvés, lancement de l'extraction...")
        
        result = subprocess.run(
            ["python3", script_path],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=BASE_DIR
        )
        
        print(f"STDOUT: {result.stdout}")
        if result.stderr:
            print(f"STDERR: {result.stderr}")
        
        if result.returncode == 0:
            fichier_manuel = os.path.join(DOSSIER_PDF, "manuel_vols.pdf")
            if os.path.exists(fichier_manuel):
                return {
                    "status": "success",
                    "message": "Extraction terminée avec succès",
                    "fichier": fichier_manuel,
                    "stdout": result.stdout
                }
            else:
                return {
                    "status": "error",
                    "message": "Le script a terminé mais le fichier manuel_vols.pdf n'a pas été généré",
                    "stdout": result.stdout,
                    "stderr": result.stderr
                }
        else:
            return {
                "status": "error",
                "message": f"Erreur lors de l'extraction (code {result.returncode})",
                "stdout": result.stdout,
                "stderr": result.stderr
            }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": "L'extraction a pris trop de temps (> 5 minutes)"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur inattendue: {str(e)}"
        }

if __name__ == "__main__":
    if MODE_BACK4APP:
        # Mode production sur Back4app
        port = int(os.environ.get("PORT", 8001))
        print(f"🚀 Serveur Back4app démarré sur le port {port}")
        uvicorn.run("mon_api:app", host="0.0.0.0", port=port)
    else:
        # Mode développement local
        if not os.path.exists(CERT_FILE) or not os.path.exists(KEY_FILE):
            print("❌ Certificats SSL manquants. Génération automatique...")
            generer_certificat()
        
        if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
            print(f"🔐 Serveur HTTPS démarré sur https://localhost:8001")
            print(f"📁 Certificat : {CERT_FILE}")
            print(f"📁 Clé : {KEY_FILE}")
            uvicorn.run(
                "mon_api:app",
                host="0.0.0.0",
                port=8001,
                ssl_keyfile=KEY_FILE,
                ssl_certfile=CERT_FILE
            )
        else:
            print("❌ Impossible de démarrer en HTTPS. Vérifiez les certificats.")
            print("   Vous pouvez toujours démarrer en HTTP avec:")
            print("   uvicorn.run('mon_api:app', host='0.0.0.0', port=8001)")
