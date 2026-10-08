"""Regression tests for tenant isolation and inventory/accounting safety."""
import csv
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Invoice, InvoiceItem, Product


class InvoiceSafetyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="safe-test-password-2026")
        self.other = User.objects.create_user("other", password="safe-test-password-2026")
        self.soap = Product.objects.create(
            owner=self.owner, name="Soap", price=Decimal("2.50"), stock=10
        )
        self.client.force_login(self.owner)

    def sale(self, ids=None, quantities=None, discount="0"):
        return self.client.post(reverse("create_invoice"), {
            "product_id[]": ids if ids is not None else [str(self.soap.pk)],
            "quantity[]": quantities if quantities is not None else ["2"],
            "discount": discount, "customer_name": "Customer",
            "customer_phone": "+263771000000", "price[]": ["0.01"],
        })

    def edit(self, invoice, quantity):
        item = invoice.items.get()
        return self.client.post(reverse("edit_invoice", args=[invoice.pk]), {
            "item_id[]": [str(item.pk)], "description[]": [item.description],
            "quantity[]": [str(quantity)], "price[]": [str(item.price)],
            "discount": "0", "customer_name": "Customer",
        })

    def test_valid_sale_uses_database_price(self):
        self.assertEqual(self.sale().status_code, 302)
        invoice = Invoice.objects.get(owner=self.owner)
        self.assertEqual(invoice.items.get().price, Decimal("2.50"))
        self.assertEqual(invoice.total, Decimal("5.00"))
        self.assertTrue(invoice.invoice_number.startswith("INV-"))
        self.soap.refresh_from_db()
        self.assertEqual(self.soap.stock, 8)

    def test_invalid_second_line_rolls_back_first(self):
        rice = Product.objects.create(
            owner=self.owner, name="Rice", stock=1, price=Decimal("5.00")
        )
        self.sale(ids=[str(self.soap.pk), str(rice.pk)], quantities=["2", "4"])
        self.assertFalse(Invoice.objects.exists())
        self.soap.refresh_from_db()
        rice.refresh_from_db()
        self.assertEqual((self.soap.stock, rice.stock), (10, 1))

    def test_duplicate_rows_cannot_oversell(self):
        self.sale(ids=[str(self.soap.pk), str(self.soap.pk)], quantities=["6", "6"])
        self.assertFalse(Invoice.objects.exists())
        self.soap.refresh_from_db()
        self.assertEqual(self.soap.stock, 10)

    def test_invalid_discount_cannot_sell(self):
        self.sale(discount="1000")
        self.assertFalse(Invoice.objects.exists())

    def test_cross_account_products_not_allowed(self):
        foreign = Product.objects.create(
            owner=self.other, name="Foreign", stock=20, price=Decimal("5.00")
        )
        self.sale(ids=[str(foreign.pk)], quantities=["1"])
        self.assertFalse(Invoice.objects.exists())

    def test_negative_quantity_cannot_sell(self):
        self.sale(quantities=["-2"])
        self.assertFalse(Invoice.objects.exists())

    def test_editing_quantity_reconciles_stock(self):
        self.sale()
        invoice = Invoice.objects.get(owner=self.owner)
        self.assertEqual(self.edit(invoice, 4).status_code, 302)
        self.soap.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(self.soap.stock, 6)
        self.assertEqual(invoice.total, Decimal("10.00"))

    def test_editing_beyond_stock_is_atomic(self):
        self.sale()
        invoice = Invoice.objects.get(owner=self.owner)
        self.edit(invoice, 50)
        self.soap.refresh_from_db()
        self.assertEqual(self.soap.stock, 8)
        self.assertEqual(invoice.items.get().quantity, 2)

    def test_csv_export_is_user_scoped(self):
        self.sale()
        Invoice.objects.create(
            owner=self.other, invoice_number="FOREIGN-PRIVATE", customer_name="Secret"
        )
        result = self.client.get(reverse("export_invoices_csv"))
        self.assertEqual(result.status_code, 200)
        rows = list(csv.reader(StringIO(result.content.decode("utf-8"))))
        self.assertEqual(len(rows), 2)
        self.assertNotIn("Secret", result.content.decode("utf-8"))

    def test_cross_account_invoices_inaccessible(self):
        foreign = Invoice.objects.create(owner=self.other, invoice_number="FOREIGN-1")
        for name in ("invoice_detail", "invoice_pdf", "edit_invoice"):
            self.assertEqual(
                self.client.get(reverse(name, args=[foreign.pk])).status_code, 404
            )

    def test_private_export_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("export_invoices_csv"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_database_diagnostic_removed(self):
        self.client.logout()
        self.assertEqual(self.client.get("/db-check/").status_code, 404)


    def test_pdf_supports_long_invoices(self):
        invoice = Invoice.objects.create(
            owner=self.owner, invoice_number="INV-LONG",
            customer_name="Customer", customer_phone="+263770000000",
        )
        InvoiceItem.objects.bulk_create([
            InvoiceItem(
                invoice=invoice, description=f"Service line {i} with a descriptive product title",
                quantity=1, price=Decimal("1.25"),
            )
            for i in range(45)
        ])
        invoice.update_total()
        response = self.client.get(reverse("invoice_pdf", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 3000)

    def test_csv_blocks_spreadsheet_formula_injection(self):
        Invoice.objects.create(
            owner=self.owner, invoice_number="INV-CSV", customer_name="=HYPERLINK()"
        )
        response = self.client.get(reverse("export_invoices_csv"))
        rows = list(csv.reader(StringIO(response.content.decode("utf-8"))))
        self.assertEqual(rows[1][1], "'=HYPERLINK()")

    def test_renamed_products_reject_unsafe_quantity_edits(self):
        self.sale()
        invoice = Invoice.objects.get(owner=self.owner)
        self.soap.name = "New product name"
        self.soap.save(update_fields=["name"])
        self.edit(invoice, 4)
        self.assertEqual(invoice.items.get().quantity, 2)
        self.soap.refresh_from_db()
        self.assertEqual(self.soap.stock, 8)

    def test_inventory_stats_count_low_and_out_separately(self):
        self.soap.stock = 0
        self.soap.save(update_fields=["stock"])
        response = self.client.get(reverse("products"))
        self.assertEqual(response.context["low_stock_count"], 0)
        self.assertEqual(response.context["out_of_stock_count"], 1)
        self.assertEqual(response.context["inventory_retail_value"], Decimal("0.00"))

    def test_product_negative_price_is_rejected(self):
        self.client.post(reverse("add_product"), {
            "name": "Unsafe product", "price": "-1.00", "stock": "2"
        })
        self.assertFalse(Product.objects.filter(name="Unsafe product").exists())

    def test_negative_restock_does_not_modify_stock(self):
        self.client.post(reverse("restock"), {
            "product_id": self.soap.pk, "quantity": "-10"
        })
        self.soap.refresh_from_db()
        self.assertEqual(self.soap.stock, 10)

    def test_policy_pages_visible_before_sign_up(self):
        self.client.logout()
        for name in ("terms", "privacy"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)



class RegistrationTests(TestCase):
    def test_weak_password_fails(self):
        self.client.post(reverse("register"), {
            "username": "newaccount", "password": "123"
        })
        self.assertFalse(User.objects.filter(username="newaccount").exists())

    def test_strong_password_creates_profile(self):
        self.client.post(reverse("register"), {
            "username": "newaccount", "email": "new@example.com",
            "password": "unique-long-example-password-2026",
        })
        user = User.objects.get(username="newaccount")
        self.assertTrue(user.companyprofile)

    def test_legacy_registration_uses_same_validation(self):
        self.client.post("/accounts/register/", {
            "username": "legacy", "password": "123"
        })
        self.assertFalse(User.objects.filter(username="legacy").exists())
