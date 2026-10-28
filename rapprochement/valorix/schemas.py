"""Schémas Pydantic pour l'extraction LLM (PDF)."""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class TransactionExtraite(BaseModel):
    numero_transaction: str = Field(description="Numéro de la transaction.")
    date_transaction: datetime = Field(
        description="Date et heure de la transaction telles que présentes dans le PDF."
    )
    numero_compte: str = Field(description="Numéro du compte concerné.")
    nomComplet: str = Field(description="Nom complet du client ou titulaire du compte.")
    debit: Optional[Decimal] = Field(description="Montant débité")
    credit: Optional[Decimal] = Field(description="Montant crédité ")
    references: str = Field(description="Référence de la transaction.")
    type_transaction: str = Field(
        description=(
            "Catégorie de transaction parmi : Dépôt chèques, "
            "Retrait chèques, Others, Dépôt espèces, Retrait espèces, "
            "Transferts, Dépôt/Virements, Retrait/Virements."
        )
    )


class ExtractionValorix(BaseModel):
    transactions: list[TransactionExtraite] = Field(
        description="Toutes les transactions présentes dans le PDF, dans l'ordre."
    )


class LigneDocumentAutre(BaseModel):
    numero_compte_client: str = Field(description="Numéro de compte du client.")
    date_transaction: str = Field(description="Date de la transaction au format YYYY-MM-DD.")
    montant_net: str = Field(
        description="Montant net tel qu'écrit dans le document (nombre, sans devise)."
    )


class ExtractionDocumentAutre(BaseModel):
    transactions: list[LigneDocumentAutre] = Field(
        description="Toutes les lignes du document, dans l'ordre."
    )
