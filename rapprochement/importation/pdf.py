"""Extraction de texte PDF et appels LLM mutualisés pour tous les imports."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

TAILLE_MAX_PDF = 50 * 1024 * 1024  # 50 Mo


class ExtractionError(Exception):
    """Erreur lisible renvoyée telle quelle à l'utilisateur."""


def creer_llm():
    """Construit le client LLM (isolé pour pouvoir être remplacé en test)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        temperature=0,
        max_tokens=16384,
        timeout=180,
        max_retries=2,
    )


def verifier_fichier_pdf(fichier):
    if not fichier.name.lower().endswith(".pdf"):
        raise ExtractionError("Le fichier doit avoir l'extension .pdf.")
    if fichier.size == 0 or fichier.size > TAILLE_MAX_PDF:
        raise ExtractionError("Fichier PDF vide ou supérieur à 50 Mo.")


def lire_pages_pdf(fichier):
    """Retourne la liste [(numéro_de_page, texte)] des pages non vides."""
    import pdfplumber

    verifier_fichier_pdf(fichier)

    try:
        fichier.seek(0)
        with pdfplumber.open(fichier) as pdf:
            pages = [
                (numero, (page.extract_text() or "").strip())
                for numero, page in enumerate(pdf.pages, start=1)
            ]
    except Exception as exc:
        logger.exception("Impossible de lire le PDF.")
        raise ExtractionError(
            "Impossible de lire le PDF. Vérifiez qu'il est valide et lisible."
        ) from exc

    pages = [(n, t) for n, t in pages if t]

    if not pages:
        raise ExtractionError(
            "Aucun texte exploitable dans le PDF (document scanné ? "
            "une étape d'OCR serait nécessaire)."
        )

    return pages


def regrouper_pages(pages, pages_par_bloc=2):
    """Regroupe des pages ENTIÈRES (jamais de ligne coupée)."""
    blocs, courant = [], []
    for numero, texte in pages:
        courant.append(f"--- PAGE {numero} ---\n{texte}")
        if len(courant) == pages_par_bloc:
            blocs.append("\n\n".join(courant))
            courant = []
    if courant:
        blocs.append("\n\n".join(courant))
    return blocs


def extraire_par_blocs(
    blocs, schema, system_prompt, intitule, max_workers=6
):
    """
    Envoie chaque bloc au LLM en parallèle et retourne les lignes
    extraites, dans l'ordre du document.

    ``schema`` doit exposer un attribut ``transactions`` (liste).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    structured_llm = creer_llm().with_structured_output(
        schema, method="json_schema"
    )
    total = len(blocs)

    def traiter(index, bloc):
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    f"Bloc {index}/{total} de : {intitule}.\n"
                    "Extrais toutes les transactions présentes dans ce bloc.\n\n"
                    f"{bloc}"
                )
            ),
        ]
        return structured_llm.invoke(messages).transactions

    lignes = []
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, total))) as ex:
            futures = [
                ex.submit(traiter, i, bloc) for i, bloc in enumerate(blocs, 1)
            ]
            for future in futures:
                lignes.extend(future.result())
    except Exception as exc:
        logger.exception("Erreur pendant l'extraction par le LLM.")
        raise ExtractionError(
            "L'extraction automatique a échoué. Vérifiez la configuration "
            "du modèle (OPENAI_API_KEY) puis réessayez."
        ) from exc

    return lignes
