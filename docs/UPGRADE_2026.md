# LedgerLite upgrade — 8 October 2026

## Scope
A backwards-compatible operational hardening and dashboard release. No model
changes or database migrations are introduced in this phase.

- Account-scoped CSV exports at `/invoices/export.csv`.
- Invoice creation with atomic stock reductions, aggregate validation for
  duplicate product rows and database-owned prices. A failed sale rolls back.
- Invoice quantity edits reconcile stock only when exactly one matching product
  exists under that account. If the original product was renamed or duplicated,
  changing quantity is rejected rather than guessing and corrupting stock.
- Product and restock input validation.
- Registration password validation and legacy route consolidation.
- Production DEBUG disabled by default; production SECRET_KEY required.
- Database diagnostic endpoint removed.
- Responsive dashboard with accurate `Total invoiced` labeling.

## Deployment prerequisites (Render)
1. Take an independently restorable PostgreSQL backup before deployment.
2. Set `SECRET_KEY` to a long random secret (do not rotate existing key casually).
3. Set `DEBUG=false` and confirm `DATABASE_URL` points to the existing
   persistent PostgreSQL database, never an ephemeral SQLite file.
4. Set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` for each actual domain.
5. If company logos are stored on Cloudinary, keep all three
   `CLOUDINARY_*` variables configured and verify old logo URLs still work.
   Missing Cloudinary variables select local file storage for development,
   which is **not** persistent on Render.
6. Install the new requirements and run Django checks/tests in a safe environment.
7. Review the Render build and startup commands before promoting. The existing
   `build.sh` invokes migrations: never deploy against an unintended database.
8. Smoke-test login, create invoice, inventory, PDF, public share, CSV, profile,
   and support before announcing the release.

## Payment accounting
Invoice totals mean **billed**, not cash collected. This release does not
implement payment processing, balances due or recurring subscriptions. Those
need a durable payment ledger and reconciliation before being advertised.

## Important data behavior
Deleting an invoice does not automatically restock products, to avoid
silently restoring stock for an already fulfilled sale. Use the inventory
restock flow for intentional adjustments.

## Local
Set `DEBUG=true` and a development `SECRET_KEY`.
```sh
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py test invoices
python manage.py runserver
```
