import os
import glob
import re
import csv
from datetime import datetime
import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import cm

# ==================== CHEMINS ====================
BASE_DIR = "/home/daniel/robot_vols"
DOSSIER_PDF = os.path.join(BASE_DIR, "xpdfs")
CHEMIN_SORTIE = os.path.join(DOSSIER_PDF, "manuel_vols.pdf")
HISTORIQUE_CSV = os.path.join(BASE_DIR, "historique_vols.csv")

# ==================== LISTE DES COMPAGNIES ====================
COMPAGNIES_CONNUES = [
    "Air France", "KLM", "Iberia", "Vueling", "TAP Air Portugal", "Azul",
    "easyJet", "LATAM", "GOL", "British Airways", "Lufthansa", "Swiss",
    "Ryanair", "Transavia", "Qatar Airways", "Emirates", "Turkish Airlines",
    "Delta", "American Airlines", "United", "Aerolineas", "Avianca",
    "Air Europa", "Air Canada", "ITA Airways", "Brussels Airlines", "LOT",
    "Austrian Airlines", "Finnair", "SAS", "Aer Lingus", "Alitalia",
    "Air Caraïbes", "French Bee", "Corsair", "Norwegian", "Wizz Air",
    "Plusieurs compagnies"
]

# ==================== FONCTIONS ====================

def charger_durees_depuis_csv():
    """Charge les durées depuis le fichier historique_vols.csv"""
    durees = {}
    if os.path.exists(HISTORIQUE_CSV):
        try:
            with open(HISTORIQUE_CSV, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) < 7:
                        continue
                    if row[0] == "Date/Heure":
                        continue
                    date_depart = row[3].strip()
                    date_retour = row[4].strip()
                    cle = f"{date_depart.replace('/', '-')}_{date_retour.replace('/', '-')}"
                    durees[cle] = row[5]
                    print(f"   📊 Durée chargée : {cle} -> {row[5]} jours")
        except Exception as e:
            print(f"⚠️ Erreur chargement CSV : {e}")
    else:
        print(f"⚠️ Fichier CSV non trouvé : {HISTORIQUE_CSV}")
    return durees

def extraire_villes_depuis_nom_fichier(nom_fichier):
    """Extrait les villes depuis le nom du fichier PDF"""
    match = re.search(r'vol_([A-Z]{3})_([A-Z]{3})_dep_', nom_fichier)
    if match:
        return match.group(1), match.group(2)
    return "???", "???"

def extraire_dates_depuis_nom_fichier(nom_fichier):
    """Extrait les dates depuis le nom du fichier PDF"""
    match = re.search(r'dep_(\d{2}-\d{2}-\d{4})_ret_(\d{2}-\d{2}-\d{4})', nom_fichier)
    if match:
        return {
            "date_depart": match.group(1).replace('-', '/'),
            "date_retour": match.group(2).replace('-', '/')
        }
    return {"date_depart": "Inconnue", "date_retour": "Inconnue"}

def retrouver_url_depuis_nom_fichier(nom_fichier):
    """Récupère l'URL complète depuis le nom du fichier"""
    match = re.search(r'ret_(\d{2}-\d{2}-\d{4})_(flights.+)\.pdf$', nom_fichier)
    if match:
        url_chemin = match.group(2)
        if '_ucs_' in url_chemin:
            chemin, params = url_chemin.split('_ucs_', 1)
            url_chemin = chemin.replace('_', '/') + '?ucs=' + params.replace('_sort_', '&sort=')
        else:
            url_chemin = url_chemin.replace('_', '/')
        return f"https://www.kayak.fr/{url_chemin}"
    return ""

def extraire_compagnie_depuis_texte(texte):
    """Extrait la compagnie depuis le texte"""
    for comp in COMPAGNIES_CONNUES:
        if comp.lower() in texte.lower():
            return comp
    return ""

def generer_rapport_final():
    """Génère le rapport final PDF"""
    print("\n" + "="*50)
    print("📊 GÉNÉRATION DU RAPPORT FINAL")
    print("="*50)
    
    # 1. Charger les durées depuis le CSV
    print("\n📋 Chargement des durées...")
    durees = charger_durees_depuis_csv()
    print(f"   {len(durees)} durées chargées")
    
    # 2. Récupérer tous les PDFs
    fichiers_pdf = glob.glob(os.path.join(DOSSIER_PDF, "vol_*.pdf"))
    fichiers_pdf = [f for f in fichiers_pdf if "manuel_vols" not in os.path.basename(f)]
    
    if not fichiers_pdf:
        print("❌ Aucun fichier PDF trouvé")
        return False
    
    print(f"\n📄 {len(fichiers_pdf)} PDF(s) trouvé(s)")
    
    tous_les_vols = []
    villes_depart = "???"
    villes_arrivee = "???"
    
    # 3. Parcourir chaque PDF
    for pdf_path in fichiers_pdf:
        nom_fichier = os.path.basename(pdf_path)
        dates = extraire_dates_depuis_nom_fichier(nom_fichier)
        url_correspondante = retrouver_url_depuis_nom_fichier(nom_fichier)
        
        if villes_depart == "???":
            villes_depart, villes_arrivee = extraire_villes_depuis_nom_fichier(nom_fichier)
        
        # Récupérer la durée
        cle = f"{dates['date_depart'].replace('/', '-')}_{dates['date_retour'].replace('/', '-')}"
        duree = durees.get(cle, "?")
        
        print(f"\n🔍 Traitement : {nom_fichier}")
        print(f"   📅 Dates : {dates['date_depart']} -> {dates['date_retour']}")
        print(f"   ⏱️  Durée : {duree} jours")
        
        with pdfplumber.open(pdf_path) as pdf:
            texte_total = ""
            for page in pdf.pages:
                texte_page = page.extract_text() or ""
                texte_total += texte_page + "\n"
            
            # Chercher les prix
            pattern_prix = r'(\d{1,3}(?:\s?\d{3})*)\s?€'
            prix_trouves = list(re.finditer(pattern_prix, texte_total))
            
            for match in prix_trouves:
                prix_str = match.group(1).replace(' ', '').replace('\n', '').replace('\t', '')
                val_num = int(prix_str)
                
                # Filtrer les prix trop petits ou trop grands
                if val_num < 50 or val_num > 30000:
                    continue
                
                # Filtrer les publicités
                contexte = texte_total[max(0, match.start()-50):match.end()+50].lower()
                if any(mot_pub in contexte for mot_pub in ['edreams', 'annonce', 'voir l\'offre', 'avianca.com', 'achétez directement', 'booking.com', 'sans frais']):
                    continue
                
                # Trouver la compagnie
                ligne_du_prix = texte_total[:match.start()].count('\n')
                lignes = texte_total.split('\n')
                
                compagnie_trouvee = ""
                for j in range(1, 4):
                    if ligne_du_prix + j < len(lignes):
                        ligne_suivante = lignes[ligne_du_prix + j]
                        if re.search(r'\b\d{1,2}:\d{2}\b', ligne_suivante) or re.search(r'\b[A-Z]{3}\b', ligne_suivante):
                            continue
                        if ligne_suivante in ["Le meilleur choix", "Le moins cher"]:
                            continue
                        comp = extraire_compagnie_depuis_texte(ligne_suivante)
                        if comp and comp not in compagnie_trouvee:
                            if compagnie_trouvee:
                                compagnie_trouvee += ", "
                            compagnie_trouvee += comp
                
                if not compagnie_trouvee:
                    continue
                
                # Éviter les doublons
                vol_existe = False
                for vol in tous_les_vols:
                    if (vol["ValeurPrix"] == val_num and 
                        vol["Compagnie"] == compagnie_trouvee and 
                        vol["URL"] == url_correspondante):
                        vol_existe = True
                        break
                
                if not vol_existe:
                    tous_les_vols.append({
                        "Prix": f"{val_num} €",
                        "ValeurPrix": val_num,
                        "Compagnie": compagnie_trouvee,
                        "Départ": dates["date_depart"],
                        "Retour": dates["date_retour"],
                        "Durée": duree,
                        "URL": url_correspondante
                    })
                    print(f"   ✅ Vol trouvé : {val_num}€ - {compagnie_trouvee}")
    
    # 4. Trier par prix
    tous_les_vols.sort(key=lambda x: x["ValeurPrix"])
    
    # 5. Statistiques
    stats_compagnies = {}
    for vol in tous_les_vols:
        comp = vol["Compagnie"]
        stats_compagnies[comp] = stats_compagnies.get(comp, 0) + 1
    
    print(f"\n📊 {len(tous_les_vols)} vols trouvés au total")
    
    # 6. Générer le PDF final
    doc = SimpleDocTemplate(
        CHEMIN_SORTIE, 
        pagesize=A4, 
        rightMargin=2*cm, 
        leftMargin=2*cm, 
        topMargin=2*cm, 
        bottomMargin=2*cm
    )
    elements = []
    styles = getSampleStyleSheet()
    
    # Styles personnalisés
    style_titre = ParagraphStyle(
        'TitrePerso',
        parent=styles['Title'],
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=colors.HexColor("#1a1a2e"),
        alignment=1,
        spaceAfter=6
    )
    
    style_sous_titre = ParagraphStyle(
        'SousTitre',
        parent=styles['Normal'],
        fontSize=12,
        textColor=colors.HexColor("#666666"),
        alignment=1,
        spaceAfter=12
    )
    
    style_entete = ParagraphStyle(
        'Entete',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        textColor=colors.white,
        alignment=1
    )
    
    style_cellule = ParagraphStyle(
        'Cellule',
        parent=styles['Normal'],
        fontSize=9,
        alignment=0
    )
    
    style_url = ParagraphStyle(
        'URL',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor("#0000EE"),
        underline=True
    )
    
    # Titre
    titre = f"Rapport Final des Vols de {villes_depart} à {villes_arrivee}"
    elements.append(Paragraph(titre, style_titre))
    elements.append(Spacer(1, 4))
    
    # Date de génération
    elements.append(Paragraph(
        f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}", 
        style_sous_titre
    ))
    elements.append(Spacer(1, 8))
    
    # Statistiques
    if tous_les_vols:
        prix_min = min(v["ValeurPrix"] for v in tous_les_vols)
        prix_max = max(v["ValeurPrix"] for v in tous_les_vols)
        prix_moyen = sum(v["ValeurPrix"] for v in tous_les_vols) / len(tous_les_vols)
    else:
        prix_min = prix_max = prix_moyen = 0
    
    stats_text = f"""
    <b>📊 Statistiques :</b><br/>
    • Prix minimum : <b>{prix_min} €</b><br/>
    • Prix maximum : <b>{prix_max} €</b><br/>
    • Prix moyen : <b>{prix_moyen:.0f} €</b><br/>
    • Nombre de vols : <b>{len(tous_les_vols)}</b><br/>
    • Compagnies : <b>{len(stats_compagnies)}</b>
    """
    elements.append(Paragraph(stats_text, styles['Normal']))
    elements.append(Spacer(1, 10))
    
    # Tableau des vols
    if tous_les_vols:
        data = []
        data.append([
            Paragraph("<b>Prix</b>", style_entete),
            Paragraph("<b>Compagnie</b>", style_entete),
            Paragraph("<b>Départ</b>", style_entete),
            Paragraph("<b>Retour</b>", style_entete),
            Paragraph("<b>Durée</b>", style_entete),
            Paragraph("<b>Lien</b>", style_entete)
        ])
        
        for vol in tous_les_vols:
            if vol["URL"]:
                lien = Paragraph(
                    f'<a href="{vol["URL"]}" color="#0000EE"><u>🔗 Voir</u></a>', 
                    style_url
                )
            else:
                lien = Paragraph("—", style_cellule)
            
            duree_texte = f"{vol['Durée']} jours" if vol['Durée'] != "?" else "?"
            
            data.append([
                Paragraph(f"<b>{vol['Prix']}</b>", style_cellule),
                Paragraph(vol["Compagnie"], style_cellule),
                Paragraph(vol["Départ"], style_cellule),
                Paragraph(vol["Retour"], style_cellule),
                Paragraph(duree_texte, style_cellule),
                lien
            ])
        
        tableau = Table(data, colWidths=[2.5*cm, 4*cm, 2.5*cm, 2.5*cm, 2*cm, 2.5*cm])
        
        tableau.setStyle(TableStyle([
            # En-tête
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#ff690f")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            
            # Lignes
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor("#1a1a2e")),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),
            ('ALIGN', (2, 1), (4, -1), 'CENTER'),
            ('ALIGN', (5, 1), (5, -1), 'CENTER'),
            
            # Bordures
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
            
            # Alternance des couleurs
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
            
            # Padding
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]))
        
        elements.append(tableau)
    
    # Pied de page
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(
        "🔒 Données extraites automatiquement par Robot Vols", 
        styles['Normal']
    ))
    
    doc.build(elements)
    
    print(f"\n✅ Rapport final généré avec succès : {CHEMIN_SORTIE}")
    print(f"📄 {len(tous_les_vols)} vols inclus")
    print("="*50)
    
    return True

# ==================== EXÉCUTION ====================
if __name__ == "__main__":
    generer_rapport_final()
