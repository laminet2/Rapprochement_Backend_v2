"""Fonctions de conversion et de détection de doublons partagées par les imports."""

import re
import unicodedata
from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

CENT = Decimal("0.01")


def normaliser_texte(valeur):
    """Minuscules, sans accents, espaces uniformisés."""
    texte = str(valeur or "").strip().lower()
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return " ".join(texte.split())


def normaliser_entete(valeur):
    """Version pour comparer des en-têtes de colonnes : lettres/chiffres seulement."""
    texte = normaliser_texte(valeur)
    texte = re.sub(r"[^a-z0-9]+", " ", texte)
    return " ".join(texte.split())


def convertir_montant(valeur):
    """
    Convertit une valeur (nombre Excel, texte « 1 234,56 »…) en Decimal.
    Renvoie None pour une cellule vide. Lève ValueError si invalide.
    """
    if valeur is None:
        return None

    if isinstance(valeur, str):
        texte = valeur.strip()
        if not texte:
            return None
        texte = texte.replace("\xa0", "").replace(" ", "")
        if "," in texte and "." in texte:
            texte = texte.replace(".", "").replace(",", ".")
        elif "," in texte:
            texte = texte.replace(",", ".")
        valeur = texte

    try:
        montant = Decimal(str(valeur))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"Montant invalide : {valeur!r}")

    if not montant.is_finite():
        raise ValueError(f"Le montant doit être un nombre fini : {valeur!r}")

    return montant


FORMATS_DATE = (
    "%d/%m/%Y %H:%M:%S.%f",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y",
    "%d.%m.%Y",
)


def convertir_date(valeur, epoch=None):
    """
    Convertit une date Excel, un nombre sériel ou un texte en datetime
    (aware si USE_TZ). Lève ValueError si invalide.
    """
    if valeur is None or valeur == "":
        raise ValueError("Date de transaction manquante.")

    if isinstance(valeur, datetime):
        resultat = valeur
    elif isinstance(valeur, date):
        resultat = datetime.combine(valeur, time.min)
    elif isinstance(valeur, (int, float)):
        from openpyxl.utils.datetime import from_excel

        resultat = from_excel(valeur, epoch=epoch) if epoch else from_excel(valeur)
        if isinstance(resultat, time):
            raise ValueError("La cellule contient une heure sans date.")
        if isinstance(resultat, date) and not isinstance(resultat, datetime):
            resultat = datetime.combine(resultat, time.min)
        if not isinstance(resultat, datetime):
            raise ValueError("Date Excel invalide.")
    elif isinstance(valeur, str):
        texte = valeur.strip()
        resultat = None
        for fmt in FORMATS_DATE:
            try:
                resultat = datetime.strptime(texte, fmt)
                break
            except ValueError:
                continue
        if resultat is None:
            raise ValueError(f"Format de date invalide : {texte!r}")
    else:
        raise ValueError(
            f"Type de date non pris en charge : {type(valeur).__name__}"
        )

    if settings.USE_TZ and timezone.is_naive(resultat):
        resultat = timezone.make_aware(resultat, timezone.get_current_timezone())

    return resultat


def montant_vers_texte(valeur):
    """Decimal -> texte à 2 décimales (ou None)."""
    if valeur is None:
        return None
    return str(Decimal(valeur).quantize(CENT))


def montant_positif(valeur):
    """Renvoie le Decimal si > 0, sinon None (0 et vide = pas de montant)."""
    if valeur is None:
        return None
    valeur = Decimal(valeur)
    return valeur if valeur > 0 else None


def a_zero(valeur):
    """Montant normalisé pour comparer : None -> 0.00."""
    return Decimal(valeur or 0).quantize(CENT)


def marquer_doublons(cles_entrantes, compteur_existant):
    """
    Pour chaque clé entrante, indique si elle existe déjà en base.

    La n-ième occurrence d'une clé dans le fichier est un doublon si la
    base contient déjà au moins n lignes avec cette clé. Ainsi deux lignes
    réellement identiques dans un même relevé ne sont pas confondues avec
    un double import, mais un relevé réimporté l'est.
    """
    vus = Counter()
    resultat = []
    for cle in cles_entrantes:
        vus[cle] += 1
        resultat.append(vus[cle] <= compteur_existant.get(cle, 0))
    return resultat
