# Rapprochement — backend

API Django REST de rapprochement entre **relevés bancaires** et **relevés Valorix**
(ou tout autre document de transactions).

```bash
pip install -r requirement.txt
cd rapprochement
cp .env.example .env        # puis renseigner OPENAI_API_KEY
python manage.py migrate
python manage.py runserver
python manage.py test        # 108 tests
```

> Les permissions sont volontairement désactivées pendant la phase de
> développement des API (aucune authentification requise).

## Parcours

1. **Importer** : le fichier est lu et renvoyé en JSON *sans rien enregistrer*.
2. **Vérifier** : le front affiche les lignes (erreurs et doublons signalés), l'utilisateur corrige.
3. **Valider** : le JSON vérifié est envoyé à `save/` → création d'un **lot d'import**.
4. **Générer** les propositions (règles actives, 1-1 / 1-N / N-1).
5. **Réviser** : rejeter certaines propositions, puis *valider tout le reste* — ou rapprocher à la main.
6. **Non rapprochées** : lister, supprimer.

## Endpoints

| Méthode | URL | Rôle |
|---|---|---|
| POST | `/rbc/compte/<id>/` | PDF bancaire → lignes à vérifier (`fichier`) |
| POST | `/rbc/save/` | Enregistre les lignes vérifiées (`compte`, `nom_fichier`, `ignorer_doublons`, `transactions`) |
| GET/POST/… | `/rbc/comptes/`, `/rbc/banques/` | Comptes et banques |
| GET/PATCH/DELETE | `/rbc/transactions/` | Filtres : `compte`, `type_transaction`, `lot`, `q`, `date_min`, `date_max`, `sens`, `rapprochee` |
| POST | `/valorix/` | Relevé Valorix `.xlsx` ou `.pdf` → lignes à vérifier |
| POST | `/valorix/autres/` | Document « autre » `.xlsx`/`.csv`/`.pdf` (`fichier`, `sens` = DEBIT/CREDIT) |
| GET | `/valorix/autres/modele/` | Modèle Excel à télécharger |
| GET | `/valorix/autres/colonnes/` | Colonnes obligatoires (texte d'avertissement) |
| POST | `/valorix/save/` | Enregistre les lignes vérifiées (relevé ou document autre, déduit de `types`) |
| GET/PATCH/DELETE | `/valorix/transactions/` | Filtres : `types`, `lot`, `q`, `date_min`, `date_max`, `sens`, `rapprochee` |
| GET | `/imports/` · POST `/imports/<id>/annuler/` | Historique des imports, annulation d'un lot |
| GET/POST/PATCH | `/rapprochements/regles/` | Une règle par type de transaction |
| POST | `/rapprochements/regles/par-defaut/` | Crée les règles manquantes |
| POST | `/rapprochements/generer/` | `lot_valorix`, `lot_banque`, `types`, `inclure_regroupements` (tout optionnel) |
| GET | `/rapprochements/` | Liste paginée : `statut_validation`, `resultat`, `origine`, `score_min`, `score_max`, `type_rapprochement`, `type_valorix`, `lot_valorix`, `lot_banque`, `q`, `ordering`, `page`, `page_size` |
| GET | `/rapprochements/<id>/` | Détail (+ `score_details` avec explications pour l'infobulle) |
| POST | `/rapprochements/<id>/valider/` · `rejeter/` · `annuler/` | `rejeter` et `annuler` demandent `{"motif": "..."}` |
| POST | `/rapprochements/decision-groupee/` | `{"ids_rejetes": [...], "motif_rejet": "...", "lot_valorix": [...]}` : rejette puis valide le reste |
| POST | `/rapprochements/manuel/apercu/` | Totaux / écart / tolérance d'une sélection |
| POST | `/rapprochements/manuel/` | Rapprochement manuel (`forcer` + `motif` si écart hors tolérance) |
| GET | `/rapprochements/non-rapprochees/?source=banque\|valorix` | Transactions sans rapprochement validé |
| POST | `/rapprochements/non-rapprochees/supprimer/` | `{"source": "banque", "ids": [...]}` |
| POST | `/rapprochements/nettoyer-bloquees/` | Annule les propositions devenues impossibles |
| GET | `/rapprochements/statistiques/` | Compteurs et taux de rapprochement |

## Règles

Une règle par type Valorix : sens bancaire attendu, fenêtre de dates (avant/après),
tolérance fixe et en %, `regroupement_max` (1 = rapprochements 1-1 uniquement) et `actif`.
Ses paramètres sont recopiés dans chaque proposition (`regle_snapshot`) : la validation
reste cohérente même si la règle change ensuite.

## Rapprochements 1-1, 1-N, N-1

La tolérance s'applique à la **somme** des montants. Les regroupements ne sont cherchés
que pour les lignes sans candidat 1-1, et une ligne n'est utilisée que dans une proposition
de regroupement par génération. La recherche est bornée (40 candidats, 200 000 nœuds).
Le rapprochement manuel accepte aussi N-M.

## Score (0-100)

Montant 30 · date 15 · référence 25 · compte 15 · nom 10 · sens 5, **normalisé sur les critères
disponibles** (un document « autre » n'a ni référence ni nom : un candidat parfait fait 100).
Chaque critère du `score_details` contient `score`, `maximum`, `disponible` et `explication`.

## Document « autre »

Colonnes obligatoires : **Numéro compte client**, **Date transaction**, **Montant net**
(détection tolérante des en-têtes, ligne d'en-têtes cherchée dans les 30 premières lignes).
Type Valorix `DOCUMENT_AUTRE` → sa propre règle. Montants négatifs refusés.
