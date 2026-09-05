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

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# ==================== CHEMINS ====================
BASE_DIR = "/home/daniel/robot_vols"
DOSSIER_PDF = os.path.join(BASE_DIR, "xpdfs")
CSV_AEROPORTS_PATH = os.path.join(BASE_DIR, "tous_les_aeroports_mondiaux.csv")
HISTORIQUE_CSV = os.path.join(BASE_DIR, "historique_vols.csv")
DOSSIER_ENREGISTREMENT = os.path.join(BASE_DIR, "enregistrement_ip")
FICHIER_VISITES = os.path.join(DOSSIER_ENREGISTREMENT, "visites.txt")
DOSSIER_CERTS = os.path.join(BASE_DIR, "certs")
CERT_FILE = os.path.join(DOSSIER_CERTS, "cert.pem")
KEY_FILE = os.path.join(DOSSIER_CERTS, "key.pem")

# Créer les dossiers
os.makedirs(DOSSIER_ENREGISTREMENT, exist_ok=True)
os.makedirs(DOSSIER_PDF, exist_ok=True)
os.makedirs(DOSSIER_CERTS, exist_ok=True)

# ==================== ÉTAT GLOBAL ====================
recherche_en_cours = False
verrou_recherche = threading.Lock()
mode_livraison = "telecharger"
email_destinataire = ""

# ==================== GÉNÉRATION DU CERTIFICAT SSL ====================
def generer_certificat():
    """Génère un certificat SSL auto-signé si inexistant."""
    if not os.path.exists(CERT_FILE) or not os.path.exists(KEY_FILE):
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
            print("   Veuillez exécuter manuellement:")
            print(f"   mkdir -p {DOSSIER_CERTS}")
            print(f"   openssl req -x509 -newkey rsa:4096 -nodes -keyout {KEY_FILE} -out {CERT_FILE} -days 365 -subj '/CN=localhost'")

generer_certificat()

app = FastAPI()

# ==================== CONFIGURATION ====================
MOT_DE_PASSE = "David56380"
EMAIL_EXPEDITEUR = "bouvarddaniel@gmail.com"
MOT_DE_PASSE_EMAIL = "kgkr sjgp kphp fuos"
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
# Les templates sont chargés depuis des fichiers séparés pour éviter les erreurs de syntaxe

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

def get_html_choix():
    return """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Choix du rapport - Robot Vols</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            background: #f0f2f5; padding: 20px; min-height: 100vh;
            display: flex; align-items: center; justify-content: center;
        }
        .container { max-width: 600px; width: 100%; background: white; padding: 40px; border-radius: 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.08); margin: 20px auto; }
        .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; padding-bottom: 20px; border-bottom: 2px solid #f0f2f5; }
        .top-bar .user { color: #666; font-weight: 500; font-size: 14px; display: flex; align-items: center; gap: 8px; }
        .top-bar .user .avatar { width: 32px; height: 32px; background: linear-gradient(135deg, #ff690f, #ff8c42); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; font-weight: 600; font-size: 14px; }
        .logout-btn { background: #f0f2f5; color: #555; padding: 8px 18px; text-decoration: none; border-radius: 8px; font-size: 13px; font-weight: 500; transition: all 0.3s; }
        .logout-btn:hover { background: #d93025; color: white; }
        h1 { color: #1a1a2e; text-align: center; font-size: 28px; margin-bottom: 8px; font-weight: 700; }
        .subtitle { text-align: center; color: #888; font-size: 14px; margin-bottom: 32px; }
        .option-card { border: 2px solid #e8ecf1; border-radius: 16px; padding: 24px; margin-bottom: 16px; cursor: pointer; transition: all 0.3s ease; display: flex; align-items: center; gap: 16px; }
        .option-card:hover { border-color: #ff690f; background: #fff5eb; transform: translateY(-2px); box-shadow: 0 4px 12px rgba(255,105,15,0.15); }
        .option-card.selected { border-color: #ff690f; background: #fff5eb; box-shadow: 0 4px 12px rgba(255,105,15,0.2); }
        .option-card .icon { font-size: 32px; width: 50px; text-align: center; }
        .option-card .content { flex: 1; }
        .option-card .content h3 { font-size: 16px; color: #1a1a2e; margin-bottom: 4px; }
        .option-card .content p { font-size: 13px; color: #888; }
        .option-card input[type="radio"] { display: none; }
        .email-group { display: none; margin-top: 16px; padding: 16px; background: #f8f9fa; border-radius: 12px; }
        .email-group.active { display: block; }
        .email-group label { font-weight: 600; display: block; margin-bottom: 8px; font-size: 14px; color: #333; }
        .email-group input[type="email"] { width: 100%; padding: 12px; border: 2px solid #e8ecf1; border-radius: 10px; font-size: 16px; transition: border-color 0.3s; background: white; }
        .email-group input[type="email"]:focus { border-color: #ff690f; outline: none; box-shadow: 0 0 0 3px rgba(255,105,15,0.1); }
        .btn-submit { background: linear-gradient(135deg, #ff690f, #ff8c42); color: white; padding: 16px; border: none; border-radius: 12px; cursor: pointer; width: 100%; font-size: 18px; font-weight: 600; margin-top: 16px; transition: transform 0.2s, box-shadow 0.2s; }
        .btn-submit:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(255, 105, 15, 0.3); }
        .btn-submit:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
        .security-note { text-align: center; color: #999; font-size: 12px; margin-top: 20px; border-top: 1px solid #f0f2f5; padding-top: 16px; }
        .existing-reports { margin-top: 20px; padding: 16px; background: #f8f9fa; border-radius: 12px; }
        .existing-reports h4 { color: #1a1a2e; margin-bottom: 8px; font-size: 14px; }
        .existing-reports .file-item { display: flex; justify-content: space-between; padding: 6px 0; font-size: 12px; color: #666; border-bottom: 1px solid #eee; }
        .existing-reports .file-item:last-child { border-bottom: none; }
        .error-msg { color: #d93025; font-size: 14px; text-align: center; margin-top: 8px; background: #fde8e8; padding: 10px; border-radius: 8px; display: none; }
        .error-msg.show { display: block; }
        .info-msg { color: #155724; font-size: 14px; text-align: center; margin-top: 8px; background: #d4edda; padding: 10px; border-radius: 8px; display: none; }
        .info-msg.show { display: block; }
    </style>
</head>
<body>
    <div class="container">
        <div class="top-bar">
            <span class="user"><span class="avatar">👤</span> Connecté</span>
            <a href="/logout" class="logout-btn">Se déconnecter</a>
        </div>
        <h1>✈️ Robot Vols</h1>
        <p class="subtitle">Choisissez comment recevoir votre rapport</p>

        <form id="choixForm" method="post" action="/valider_choix">
            <div id="optionTelecharger" class="option-card selected">
                <input type="radio" name="mode" value="telecharger" checked>
                <div class="icon">⬇️</div>
                <div class="content">
                    <h3>Télécharger le rapport</h3>
                    <p>Le rapport sera téléchargé automatiquement à la fin</p>
                </div>
            </div>

            <div id="optionEmail" class="option-card">
                <input type="radio" name="mode" value="email">
                <div class="icon">📧</div>
                <div class="content">
                    <h3>Recevoir par email</h3>
                    <p>Le rapport vous sera envoyé automatiquement</p>
                </div>
            </div>

            <div id="emailGroup" class="email-group">
                <label for="emailDestinataire">📧 Adresse email :</label>
                <input type="email" id="emailDestinataire" name="email" placeholder="exemple@email.com">
            </div>

            <div id="errorMsg" class="error-msg">⚠️ Veuillez entrer une adresse email valide.</div>
            <div id="infoMsg" class="info-msg">✅ Validation OK, redirection en cours...</div>

            <button type="submit" class="btn-submit" id="submitBtn">🚀 Commencer la recherche</button>
        </form>

        <div class="existing-reports">
            <h4>📁 Rapports précédents</h4>
            {RAPPORTS_EXISTANTS}
        </div>

        <p class="security-note">🔒 Connexion sécurisée via HTTPS</p>
    </div>

    <script>
        const optionTelecharger = document.getElementById('optionTelecharger');
        const optionEmail = document.getElementById('optionEmail');
        const emailGroup = document.getElementById('emailGroup');
        const emailInput = document.getElementById('emailDestinataire');
        const errorMsg = document.getElementById('errorMsg');
        const infoMsg = document.getElementById('infoMsg');
        const submitBtn = document.getElementById('submitBtn');
        const form = document.getElementById('choixForm');

        optionTelecharger.addEventListener('click', function() {
            this.classList.add('selected');
            optionEmail.classList.remove('selected');
            document.querySelector('input[name="mode"][value="telecharger"]').checked = true;
            emailGroup.classList.remove('active');
            emailInput.required = false;
            errorMsg.classList.remove('show');
            infoMsg.classList.remove('show');
        });

        optionEmail.addEventListener('click', function() {
            this.classList.add('selected');
            optionTelecharger.classList.remove('selected');
            document.querySelector('input[name="mode"][value="email"]').checked = true;
            emailGroup.classList.add('active');
            emailInput.required = true;
            errorMsg.classList.remove('show');
            infoMsg.classList.remove('show');
        });

        form.addEventListener('submit', function(e) {
            const modeEmail = document.querySelector('input[name="mode"][value="email"]').checked;
            
            if (modeEmail) {
                const email = emailInput.value.trim();
                if (!email || !email.includes('@') || !email.includes('.')) {
                    e.preventDefault();
                    errorMsg.textContent = '⚠️ Veuillez entrer une adresse email valide.';
                    errorMsg.classList.add('show');
                    infoMsg.classList.remove('show');
                    emailInput.focus();
                    return;
                }
            }
            
            errorMsg.classList.remove('show');
            infoMsg.textContent = '✅ Validation OK, redirection en cours...';
            infoMsg.classList.add('show');
            submitBtn.disabled = true;
            submitBtn.textContent = '⏳ Redirection...';
        });

        emailInput.addEventListener('input', function() {
            if (this.value.trim() && this.value.includes('@')) {
                errorMsg.classList.remove('show');
            }
        });
    </script>
</body>
</html>"""

def get_html_form():
    return """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Saint Raoul, c'est cool!</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            background: #f0f2f5; padding: 20px; min-height: 100vh;
            display: flex; align-items: center; justify-content: center;
        }
        .container { max-width: 720px; width: 100%; background: white; padding: 40px; border-radius: 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.08); margin: 20px auto; }
        .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; padding-bottom: 20px; border-bottom: 2px solid #f0f2f5; }
        .top-bar .user { color: #666; font-weight: 500; font-size: 14px; display: flex; align-items: center; gap: 8px; }
        .top-bar .user .avatar { width: 32px; height: 32px; background: linear-gradient(135deg, #ff690f, #ff8c42); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; font-weight: 600; font-size: 14px; }
        .logout-btn { background: #f0f2f5; color: #555; padding: 8px 18px; text-decoration: none; border-radius: 8px; font-size: 13px; font-weight: 500; transition: all 0.3s; }
        .logout-btn:hover { background: #d93025; color: white; }
        .mode-info { background: #f8f9fa; padding: 12px 16px; border-radius: 10px; margin-bottom: 20px; text-align: center; font-size: 14px; color: #555; border: 1px solid #e8ecf1; }
        .mode-info strong { color: #ff690f; }
        h1 { color: #1a1a2e; text-align: center; font-size: 28px; margin-bottom: 8px; font-weight: 700; }
        .subtitle { text-align: center; color: #888; font-size: 14px; margin-bottom: 32px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { font-weight: 600; display: block; margin-bottom: 8px; color: #333; font-size: 14px; }
        .form-group input[type="text"], .form-group input[type="number"], .form-group input[type="date"] { width: 100%; padding: 14px; border: 2px solid #e8ecf1; border-radius: 12px; font-size: 16px; transition: border-color 0.3s, box-shadow 0.3s; background: #f8f9fa; }
        .form-group input:focus { border-color: #ff690f; outline: none; box-shadow: 0 0 0 4px rgba(255, 105, 15, 0.08); background: white; }
        .autocomplete-container { position: relative; width: 100%; }
        .suggestions-list { position: absolute; top: calc(100% + 4px); left: 0; right: 0; background: white; border: 2px solid #e8ecf1; border-radius: 12px; max-height: 240px; overflow-y: auto; z-index: 1000; box-shadow: 0 8px 24px rgba(0,0,0,0.12); display: none; }
        .suggestions-list.active { display: block; }
        .suggestions-list .suggestion-item { padding: 12px 16px; cursor: pointer; border-bottom: 1px solid #f0f2f5; font-size: 14px; transition: background 0.15s; color: #333; }
        .suggestions-list .suggestion-item:last-child { border-bottom: none; }
        .suggestions-list .suggestion-item:hover { background: #fff5eb; color: #ff690f; }
        .row { display: flex; gap: 16px; }
        .row .form-group { flex: 1; }
        .options-group { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 4px; }
        .option-card { flex: 1; min-width: 120px; padding: 14px 16px; border: 2px solid #e8ecf1; border-radius: 12px; cursor: pointer; text-align: center; transition: all 0.25s ease; background: #f8f9fa; user-select: none; }
        .option-card:hover { border-color: #ff8c42; background: #fff5eb; transform: translateY(-2px); box-shadow: 0 4px 12px rgba(255, 105, 15, 0.15); }
        .option-card.selected { border-color: #ff690f; background: linear-gradient(135deg, #fff5eb, #ffe8d6); box-shadow: 0 4px 12px rgba(255, 105, 15, 0.2); }
        .option-card .value { font-size: 20px; font-weight: 700; color: #1a1a2e; }
        .option-card .label { font-size: 11px; color: #888; margin-top: 2px; }
        .option-card .duration { font-size: 11px; color: #ff690f; font-weight: 500; margin-top: 4px; background: rgba(255, 105, 15, 0.08); padding: 2px 8px; border-radius: 4px; display: inline-block; }
        .option-card .tours { font-size: 11px; color: #666; margin-top: 2px; }
        .option-card input[type="radio"] { display: none; }
        .btn-submit { background: linear-gradient(135deg, #ff690f, #ff8c42); color: white; padding: 16px; border: none; border-radius: 12px; cursor: pointer; width: 100%; font-size: 18px; font-weight: 600; margin-top: 8px; transition: transform 0.2s, box-shadow 0.2s; }
        .btn-submit:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(255, 105, 15, 0.3); }
        @media (max-width: 600px) { .row { flex-direction: column; gap: 0; } .container { padding: 24px; } .option-card { min-width: 80px; padding: 10px 12px; } }
        .security-note { text-align: center; color: #999; font-size: 12px; margin-top: 20px; border-top: 1px solid #f0f2f5; padding-top: 16px; }
        .change-mode { text-align: center; margin-top: 12px; }
        .change-mode a { color: #ff690f; font-size: 13px; text-decoration: none; }
        .change-mode a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <div class="top-bar">
            <span class="user"><span class="avatar">👤</span> Connecté</span>
            <a href="/logout" class="logout-btn">Se déconnecter</a>
        </div>
        <h1>✈️ Robot Vols</h1>
        <p class="subtitle">Trouvez les meilleurs prix en quelques clics</p>
        
        <div class="mode-info">
            📦 Livraison choisie : <strong>{MODE_TEXTE}</strong>
            {EMAIL_INFO}
        </div>
        
        <form method="get" action="/lancer" id="searchForm">
            <div class="form-group">
                <label>📅 Date de départ</label>
                <input type="text" id="depart" name="depart" placeholder="JJ/MM/AAAA" value="" required>
            </div>

            <div class="row">
                <div class="form-group">
                    <label>🛫 Ville de départ</label>
                    <div class="autocomplete-container">
                        <input type="text" id="ville_depart" name="ville_depart" placeholder="Ex: Paris, Nantes..." autocomplete="off" required>
                        <div id="suggestions_depart" class="suggestions-list"></div>
                    </div>
                </div>
                <div class="form-group">
                    <label>🛬 Ville d'arrivée</label>
                    <div class="autocomplete-container">
                        <input type="text" id="ville_arrivee" name="ville_arrivee" placeholder="Ex: New York, Tokyo..." autocomplete="off" required>
                        <div id="suggestions_arrivee" class="suggestions-list"></div>
                    </div>
                </div>
            </div>

            <div class="row">
                <div class="form-group">
                    <label>📆 Durée du séjour (jours)</label>
                    <input type="number" id="duree_sejour" name="duree_sejour" placeholder="90" min="1" value="" required>
                </div>
                <div class="form-group">
                    <label>🔄 Décalages (+/-)</label>
                    <div class="options-group" id="offsetOptions">
                        <label class="option-card" data-value="0">
                            <input type="radio" name="nb_boucles" value="0" checked>
                            <div class="value">0</div>
                            <div class="label">Pas de décalage</div>
                            <div class="tours">1 tour</div>
                            <div class="duration">~30 sec</div>
                        </label>
                        <label class="option-card" data-value="1">
                            <input type="radio" name="nb_boucles" value="1">
                            <div class="value">±1</div>
                            <div class="label">Décalage léger</div>
                            <div class="tours">9 tours</div>
                            <div class="duration">~4 min</div>
                        </label>
                        <label class="option-card" data-value="2">
                            <input type="radio" name="nb_boucles" value="2">
                            <div class="value">±2</div>
                            <div class="label">Décalage moyen</div>
                            <div class="tours">25 tours</div>
                            <div class="duration">~14 min</div>
                        </label>
                        <label class="option-card" data-value="3">
                            <input type="radio" name="nb_boucles" value="3">
                            <div class="value">±3</div>
                            <div class="label">Décalage large</div>
                            <div class="tours">49 tours</div>
                            <div class="duration">~oh putain! que c'est long</div>
                        </label>
                    </div>
                    <input type="hidden" id="nb_boucles" name="nb_boucles" value="0">
                </div>
            </div>

            <button type="submit" class="btn-submit">🚀 Lancer la recherche</button>
        </form>
        
        <div class="change-mode">
            <a href="/choix">⬅️ Changer le mode de livraison</a>
        </div>
        
        <p class="security-note">🔒 Connexion sécurisée via HTTPS</p>
    </div>

    <script>
        document.querySelectorAll('.option-card').forEach(function(card) {
            card.addEventListener('click', function() {
                document.querySelectorAll('.option-card').forEach(function(c) {
                    c.classList.remove('selected');
                });
                this.classList.add('selected');
                const radio = this.querySelector('input[type="radio"]');
                radio.checked = true;
                document.getElementById('nb_boucles').value = radio.value;
            });
        });
        document.querySelector('.option-card').classList.add('selected');

        function setupAutocomplete(inputId, listId) {
            const input = document.getElementById(inputId);
            const list = document.getElementById(listId);
            let timeoutId = null;

            input.addEventListener('input', function() {
                clearTimeout(timeoutId);
                const query = this.value.trim();
                
                if (query.length < 1) {
                    list.classList.remove('active');
                    list.innerHTML = '';
                    return;
                }

                timeoutId = setTimeout(function() {
                    fetch('/villes?q=' + encodeURIComponent(query))
                        .then(function(response) { return response.json(); })
                        .then(function(data) {
                            list.innerHTML = '';
                            if (data.length > 0) {
                                data.forEach(function(item) {
                                    const div = document.createElement('div');
                                    div.className = 'suggestion-item';
                                    div.textContent = item;
                                    div.addEventListener('click', function() {
                                        input.value = item;
                                        list.classList.remove('active');
                                        list.innerHTML = '';
                                    });
                                    list.appendChild(div);
                                });
                                list.classList.add('active');
                            } else {
                                list.classList.remove('active');
                            }
                        })
                        .catch(function(err) {
                            console.error('Erreur autocomplétion:', err);
                            list.classList.remove('active');
                        });
                }, 200);
            });

            document.addEventListener('click', function(e) {
                if (!input.contains(e.target) && !list.contains(e.target)) {
                    list.classList.remove('active');
                    list.innerHTML = '';
                }
            });
        }

        setupAutocomplete('ville_depart', 'suggestions_depart');
        setupAutocomplete('ville_arrivee', 'suggestions_arrivee');
    </script>
</body>
</html>"""

def get_html_suivi(depart, ville_depart, ville_arrivee, duree_sejour, nb_boucles, mode_livraison, email_destinataire):
    html_titre = "<h1>🚀 Recherche en cours</h1>"
    html_sous_titre = "<p class='subtitle'><b>Départ :</b> " + ville_depart + " → <b>Arrivée :</b> " + ville_arrivee + "</p>"
    mode_texte = "📧 Envoi par email" if mode_livraison == "email" else "⬇️ Téléchargement automatique"
    html_mode = f"<p style='text-align:center;color:#666;font-size:13px;'>{mode_texte}</p>"
    
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Exécution - Robot Vols</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            background: #f0f2f5; 
            padding: 20px; 
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .container {{ 
            max-width: 800px; 
            width: 100%;
            background: white; 
            padding: 40px; 
            border-radius: 20px; 
            box-shadow: 0 10px 40px rgba(0,0,0,0.08); 
            margin: 20px auto; 
        }}
        .top-bar {{ 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            margin-bottom: 32px; 
            padding-bottom: 20px; 
            border-bottom: 2px solid #f0f2f5; 
        }}
        .top-bar .user {{ 
            color: #666; 
            font-weight: 500; 
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .top-bar .user .avatar {{
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, #ff690f, #ff8c42);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 600;
            font-size: 14px;
        }}
        .logout-btn {{ 
            background: #f0f2f5; 
            color: #555; 
            padding: 8px 18px; 
            text-decoration: none; 
            border-radius: 8px; 
            font-size: 13px; 
            font-weight: 500; 
            transition: all 0.3s; 
        }}
        .logout-btn:hover {{ 
            background: #d93025; 
            color: white; 
        }}
        h1 {{ color: #1a1a2e; text-align: center; font-size: 28px; margin-bottom: 8px; }}
        .subtitle {{ text-align: center; color: #888; font-size: 14px; margin-bottom: 24px; }}
        .logs {{ 
            background: #1a1a2e; 
            color: #00ff41; 
            padding: 20px; 
            border-radius: 12px; 
            margin: 20px 0; 
            font-family: 'Courier New', monospace; 
            font-size: 13px; 
            white-space: pre-wrap; 
            max-height: 500px; 
            overflow-y: auto; 
            line-height: 1.6;
        }}
        .logs a {{ 
            color: #00ff41; 
            text-decoration: underline;
        }}
        .logs a:hover {{ 
            color: #ff690f;
        }}
        .actions {{
            display: flex;
            flex-direction: column;
            gap: 16px;
            margin-top: 24px;
            padding: 24px;
            background: #f8f9fa;
            border-radius: 12px;
            border: 2px dashed #ddd;
        }}
        .actions .row {{ display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; }}
        .btn {{
            padding: 12px 28px;
            border: none;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }}
        .btn:hover {{ transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,0.15); }}
        .btn-success {{ background: linear-gradient(135deg, #00b894, #00a381); color: white; }}
        .btn-primary {{ background: linear-gradient(135deg, #ff690f, #ff8c42); color: white; }}
        .email-group {{ display: flex; gap: 10px; flex: 1; min-width: 250px; }}
        .email-group input {{
            flex: 1;
            padding: 12px 16px;
            border: 2px solid #e8ecf1;
            border-radius: 10px;
            font-size: 14px;
            transition: border-color 0.3s;
            background: white;
        }}
        .email-group input:focus {{ border-color: #ff690f; outline: none; }}
        .hidden {{ display: none !important; }}
        .status-msg {{ 
            text-align: center; 
            padding: 12px; 
            border-radius: 10px; 
            margin-top: 8px; 
            font-weight: 500; 
        }}
        .status-success {{ background: #d4edda; color: #155724; }}
        .status-error {{ background: #f8d7da; color: #721c24; }}
        .back-btn {{ 
            background: #6c757d; 
            color: white; 
            padding: 10px 24px; 
            text-decoration: none; 
            border-radius: 10px; 
            font-weight: 500; 
            transition: all 0.3s; 
        }}
        .back-btn:hover {{ background: #5a6268; }}
        .security-note {{
            text-align: center;
            color: #999;
            font-size: 12px;
            margin-top: 20px;
            border-top: 1px solid #f0f2f5;
            padding-top: 16px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="top-bar">
            <span class="user">
                <span class="avatar">👤</span>
                Connecté
            </span>
            <a href="/logout" class="logout-btn">Se déconnecter</a>
        </div>
        {html_titre}
        {html_sous_titre}
        {html_mode}
        
        <div class="logs" id="logBox">Initialisation du robot...\\n</div>
        
        <div id="actionsPanel" class="actions hidden">
            <p style="text-align:center;font-weight:600;color:#333;">✅ Recherche terminée !</p>
            
            <div class="row">
                <a href="/" class="back-btn">⬅️ Retour au formulaire</a>
            </div>
        </div>
        <p class="security-note">🔒 Connexion sécurisée via HTTPS</p>
    </div>

    <script>
        const logBox = document.getElementById('logBox');
        let modeLivraison = "{mode_livraison}";
        let emailDestinataire = "{email_destinataire}";

        function linkify(text) {{
            const urlPattern = /(https?:\\/\\/[^\\s<]+)/g;
            return text.replace(urlPattern, function(url) {{
                return '<a href="' + url + '" target="_blank">' + url + '</a>';
            }});
        }}

        async function runBot() {{
            try {{
                const params = new URLSearchParams({{
                    depart: "{depart}",
                    ville_depart: "{ville_depart}",
                    ville_arrivee: "{ville_arrivee}",
                    duree_sejour: {duree_sejour},
                    nb_boucles: {nb_boucles}
                }});
                
                const response = await fetch(`/lancer_stream?${{params}}`);
                
                if (!response.ok) {{
                    logBox.textContent += "\\n❌ Erreur serveur HTTP : " + response.status;
                    return;
                }}

                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                while (true) {{
                    const {{ value, done }} = await reader.read();
                    if (done) {{
                        logBox.innerHTML += "\\n🏁 Toutes les recherches sont terminées !\\n";
                        break;
                    }}
                    const text = decoder.decode(value, {{ stream: true }});
                    logBox.innerHTML += linkify(text);
                    logBox.scrollTop = logBox.scrollHeight;
                }}

                verifierPDF();
                
            }} catch (e) {{
                logBox.innerHTML += "\\n❌ Erreur : " + e;
            }}
        }}

        function afficherActions() {{
            const panel = document.getElementById('actionsPanel');
            if (panel) {{
                panel.classList.remove('hidden');
            }}
        }}

        function verifierPDF() {{
            fetch('/verifier_pdf')
                .then(response => response.json())
                .then(data => {{
                    if (data.exists) {{
                        logBox.innerHTML += "✅ Rapport PDF généré avec succès !\\n";
                        afficherActions();
                        if (modeLivraison === 'email' && emailDestinataire) {{
                            logBox.innerHTML += "📧 Envoi du rapport par email à " + emailDestinataire + "...\\n";
                            fetch('/envoyer_email', {{
                                method: 'POST',
                                headers: {{ 'Content-Type': 'application/json' }},
                                body: JSON.stringify({{ email: emailDestinataire }})
                            }})
                            .then(response => response.json())
                            .then(data => {{
                                if (data.status === 'success') {{
                                    logBox.innerHTML += "✅ Rapport envoyé à " + emailDestinataire + " !\\n";
                                }} else {{
                                    logBox.innerHTML += "❌ Erreur envoi email : " + data.message + "\\n";
                                }}
                            }})
                            .catch(e => {{
                                logBox.innerHTML += "❌ Erreur envoi email : " + e + "\\n";
                            }});
                        }} else if (modeLivraison === 'telecharger') {{
                            logBox.innerHTML += "⬇️ Téléchargement automatique du rapport...\\n";
                            setTimeout(function() {{
                                window.location.href = '/telecharger_pdf';
                            }}, 2000);
                        }}
                    }} else {{
                        logBox.innerHTML += "⏳ Extraction des données en cours...\\n";
                        setTimeout(() => verifierPDF(), 3000);
                    }}
                }})
                .catch(() => {{
                    setTimeout(() => verifierPDF(), 3000);
                }});
        }}

        runBot();
    </script>
</body>
</html>"""

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