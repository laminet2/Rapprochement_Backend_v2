"""Schémas Pydantic pour l'extraction LLM d'un relevé bancaire."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from releverBancaire.models import TypeTransactionBancaire

TypeTransactionEnum = Enum(
    "TypeTransactionEnum",
    {choix[0]: choix[0] for choix in TypeTransactionBancaire.choices},
)


class LigneTransactionSchema(BaseModel):
    date_transaction: str = Field(
        description="Date de transaction au format YYYY-MM-DD."
    )
    date_valeur: Optional[str] = Field(
        default=None,
        description="Date de valeur au format YYYY-MM-DD, si présente.",
    )
    numero_compte_ou_cheque: Optional[str] = Field(
        default=None,
        description=(
            "Identifiant à extraire du relevé bancaire : "
            "1. Pour une opération par chèque, extraire le numéro du chèque. "
            "2. Pour les autres opérations, extraire le numéro de compte CGF "
            "lorsqu'il est explicitement présent dans le libellé. "
            "Ce numéro est constitué exclusivement de chiffres, généralement "
            "au nombre de 7 à 10, avec des terminaisons très fréquentes en "
            "10XX ou 90XX (ex: ...1066, ...1036, ...1022, ...1008, ...9018, "
            "...9064, etc.). Si l'identifiant attendu est absent, retourner "
            "null. Ne jamais inventer, déduire ou reconstruire un numéro."
        ),
    )
    libelle_brut: str = Field(description="Libellé complet et exact de l'opération.")
    debit: Optional[float] = Field(
        default=None, description="Montant du débit, positif, sinon null."
    )
    credit: Optional[float] = Field(
        default=None, description="Montant du crédit, positif, sinon null."
    )
    solde_apres_operation: Optional[float] = Field(
        default=None, description="Solde après opération, si disponible."
    )
    type_transaction: TypeTransactionEnum = Field(
        default=TypeTransactionEnum.AUTRE,
        description="Type de transaction parmi les choix autorisés.",
    )


class ReleveBancaireSchema(BaseModel):
    transactions: List[LigneTransactionSchema] = Field(
        description="Toutes les opérations du relevé bancaire."
    )
