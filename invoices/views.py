from decimal import Decimal, InvalidOperation
import csv
import uuid
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from django.utils.text import slugify

from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import F

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils import timezone

from reportlab.pdfgen import canvas
from django.db.models import Sum
from django.db.models.functions import TruncMonth

from .models import CompanyProfile, Invoice, InvoiceItem, Product, Notification
from django.shortcuts import render
from django.shortcuts import render, redirect
from django.contrib import messages

from .forms import SupportTicketForm

from django.db import connection
from django.http import HttpResponse



# ======================
# HOME
# ======================
def home(request):
    return render(request, "home.html")


# ======================
# REGISTER
# ======================
def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        if not username or len(username) > 150:
            messages.error(request, "Enter a username under 150 characters.")
            return redirect("register")
        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("register")
        if email and User.objects.filter(email__iexact=email).exists():
            messages.error(request, "That email address is already registered.")
            return redirect("register")
        try:
            validate_password(password, user=User(username=username, email=email))
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
            return redirect("register")
        with transaction.atomic():
            user = User.objects.create_user(username=username, email=email, password=password)
            CompanyProfile.objects.get_or_create(user=user)
        login(request, user)
        return redirect("dashboard")
    return render(request, "register.html")


# ======================


# ======================
# LOGIN
# ======================
def login_view(request):

    error = None

    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST.get("username"),
            password=request.POST.get("password")
        )

        if user:
            login(request, user)
            return redirect("dashboard")

        error = "Invalid username or password."

    return render(request, "login.html", {"error": error})


# ======================
# LOGOUT
# ======================
@login_required
def logout_view(request):
    logout(request)
    return redirect("home")



# ======================
# PRODUCTS
# ======================
@login_required
def products(request):
    products = Product.objects.filter(
        owner=request.user,
        is_deleted=False
    ).order_by("name")

    low_stock_products = products.filter(stock__gt=0, stock__lte=5)

    low_stock_count = low_stock_products.count()
    out_of_stock_count = products.filter(stock__lte=0).count()
    inventory_retail_value = sum((p.inventory_value for p in products), Decimal("0.00"))

    return render(request, "products.html", {
        "products": products,
        "low_stock_count": low_stock_count,
        "out_of_stock_count": out_of_stock_count,
        "inventory_retail_value": inventory_retail_value,
    })


# ======================
# ADD PRODUCT
# ======================
@login_required
def add_product(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        try:
            price = Decimal(request.POST.get("price", "0") or "0")
            stock = int(request.POST.get("stock", "0") or "0")
            if (
                not name or len(name) > 255 or not price.is_finite()
                or price < 0 or price > Decimal("99999999.99")
                or price.as_tuple().exponent < -2
                or stock < 0 or stock > 100000000
            ):
                raise ValueError
        except (ValueError, InvalidOperation):
            messages.error(request, "Enter a valid name, non-negative price and stock quantity.")
            return redirect("add_product")
        Product.objects.create(owner=request.user, name=name, price=price, stock=stock)
        messages.success(request, "Product added.")
        return redirect("products")
    return render(request, "add_product.html")


# ======================


# ======================
# CREATE INVOICE
# ======================
@login_required
def create_invoice(request):
    run_stock_check(request.user)
    products = Product.objects.filter(owner=request.user, is_deleted=False).order_by("name")
    if request.method == "POST":
        product_ids = request.POST.getlist("product_id[]")
        quantities = request.POST.getlist("quantity[]")
        try:
            if not product_ids or len(product_ids) != len(quantities):
                raise ValueError("Add at least one complete invoice item.")
            requested = {}
            for raw_id, raw_qty in zip(product_ids, quantities):
                if not raw_id:  # Ignore an unused blank line in the form.
                    continue
                product_id = int(raw_id)
                quantity = int(raw_qty)
                if product_id <= 0 or quantity <= 0 or quantity > 100000000:
                    raise ValueError("Item quantities must be positive.")
                requested[product_id] = requested.get(product_id, 0) + quantity
            if not requested:
                raise ValueError("Select at least one product.")
            discount = Decimal(request.POST.get("discount", "0") or "0")
            if (not discount.is_finite() or discount < 0 or discount > Decimal("99999999.99")
                    or discount.as_tuple().exponent < -2):
                raise ValueError("Enter a non-negative discount with at most two decimal places.")
            with transaction.atomic():
                locked = list(
                    Product.objects.select_for_update()
                    .filter(owner=request.user, is_deleted=False, pk__in=requested)
                    .order_by("pk")
                )
                if len(locked) != len(requested):
                    raise ValueError("One or more selected products are unavailable.")
                subtotal = sum(
                    (product.price * requested[product.pk] for product in locked),
                    Decimal("0.00"),
                )
                if subtotal > Decimal("99999999.99"):
                    raise ValueError("Invoice subtotal is too large.")
                if discount > subtotal:
                    raise ValueError("Discount cannot exceed the invoice subtotal.")
                for product in locked:
                    quantity = requested[product.pk]
                    updated = Product.objects.filter(
                        pk=product.pk, owner=request.user, is_deleted=False,
                        stock__gte=quantity,
                    ).update(stock=F("stock") - quantity)
                    if not updated:
                        raise ValueError(f"Not enough stock for {product.name}.")
                invoice = Invoice.objects.create(
                    owner=request.user,
                    invoice_number=f"TMP-{uuid.uuid4().hex}",
                    customer_name=request.POST.get("customer_name", "").strip()[:255],
                    customer_phone=request.POST.get("customer_phone", "").strip()[:50],
                    discount=discount,
                )
                InvoiceItem.objects.bulk_create([
                    InvoiceItem(
                        invoice=invoice, description=product.name,
                        quantity=requested[product.pk], price=product.price,
                    )
                    for product in locked
                ])
                invoice.update_total()
                invoice.invoice_number = f"INV-{invoice.pk:05d}"
                invoice.save(update_fields=["invoice_number"])
        except (InvalidOperation, ValueError, OverflowError) as exc:
            messages.error(request, str(exc) or "Check the invoice quantities and discount.")
            return redirect("create_invoice")
        messages.success(request, f"Invoice {invoice.invoice_number} created.")
        return redirect("invoice_detail", invoice.pk)
    return render(request, "create_invoice.html", {"products": products})


# ======================


# ======================
# EDIT INVOICE
# ======================
@login_required
def edit_invoice(request, invoice_id):
    """Adjust line quantities only when original inventory can be reconciled."""
    invoice = get_object_or_404(Invoice, pk=invoice_id, owner=request.user)
    if request.method == "POST":
        try:
            ids = request.POST.getlist("item_id[]")
            descs = request.POST.getlist("description[]")
            quantities = request.POST.getlist("quantity[]")
            prices = request.POST.getlist("price[]")
            if not (len(ids) == len(descs) == len(quantities) == len(prices)):
                raise ValueError("All invoice lines are required.")
            discount = Decimal(request.POST.get("discount") or "0")
            if (not discount.is_finite() or discount < 0 or discount > Decimal("99999999.99")
                or discount.as_tuple().exponent < -2):
                raise ValueError("Enter a non-negative discount with at most two decimal places.")
            with transaction.atomic():
                invoice = get_object_or_404(
                    Invoice.objects.select_for_update(), pk=invoice_id, owner=request.user
                )
                existing = list(invoice.items.select_for_update().order_by("pk"))
                by_id = {str(item.pk): item for item in existing}
                if len(ids) != len(existing) or set(ids) != set(by_id):
                    raise ValueError("Invoice lines have changed. Refresh and try again.")
                new_values = []
                stock_deltas = {}
                subtotal = Decimal("0.00")
                for raw_id, description, raw_qty, raw_price in zip(ids, descs, quantities, prices):
                    item = by_id[raw_id]
                    quantity = int(raw_qty)
                    price = Decimal(raw_price)
                    description = description.strip()
                    if (
                        not description or len(description) > 255
                        or quantity <= 0 or quantity > 100000000
                        or not price.is_finite() or price < 0
                        or price > Decimal("99999999.99")
                        or price.as_tuple().exponent < -2
                    ):
                        raise ValueError("Invoice lines need a description, positive quantity and valid price.")
                    subtotal += quantity * price
                    delta = quantity - item.quantity
                    if delta:
                        matches = list(
                            Product.objects.select_for_update()
                            .filter(owner=request.user, name=item.description)
                            .order_by("pk")[:2]
                        )
                        if len(matches) != 1:
                            raise ValueError(
                                "Cannot adjust quantity: the original product is missing or duplicated. "
                                "Keep the existing quantity, or correct stock manually."
                            )
                        product = matches[0]
                        if delta > 0 and product.is_deleted:
                            raise ValueError("Cannot use more stock from an archived product.")
                        stock_deltas[product.pk] = stock_deltas.get(product.pk, 0) + delta
                    new_values.append((item, description, quantity, price))
                if subtotal > Decimal("99999999.99"):
                    raise ValueError("Invoice subtotal is too large.")
                if discount > subtotal:
                    raise ValueError("Discount cannot exceed the subtotal.")
                for product_id, delta in stock_deltas.items():
                    if delta > 0:
                        count = Product.objects.filter(
                            pk=product_id, owner=request.user, is_deleted=False,
                            stock__gte=delta,
                        ).update(stock=F("stock") - delta)
                        if not count:
                            raise ValueError("Insufficient stock for the updated invoice.")
                    elif delta < 0:
                        Product.objects.filter(pk=product_id, owner=request.user).update(
                            stock=F("stock") + (-delta)
                        )
                for item, description, quantity, price in new_values:
                    item.description = description
                    item.quantity = quantity
                    item.price = price
                    item.save(update_fields=["description", "quantity", "price"])
                invoice.customer_name = request.POST.get("customer_name", "").strip()[:255]
                invoice.customer_phone = request.POST.get("customer_phone", "").strip()[:50]
                invoice.discount = discount
                invoice.save(update_fields=["customer_name", "customer_phone", "discount"])
                invoice.update_total()
        except (InvalidOperation, ValueError, OverflowError) as exc:
            messages.error(request, str(exc) or "Please correct the invoice details.")
            return redirect("edit_invoice", invoice_id=invoice_id)
        messages.success(request, "Invoice changes saved and stock reconciled.")
        return redirect("invoice_detail", invoice.id)
    return render(request, "edit_invoice.html", {"invoice": invoice})


# ======================
# INVOICE DETAIL
# ======================
@login_required
def invoice_detail(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, owner=request.user)
    return render(request, "invoice_detail.html", {"invoice": invoice})


# ======================
# DELETE INVOICE
# ======================
@login_required
def delete_invoice(request, invoice_id):

    invoice = get_object_or_404(Invoice, id=invoice_id, owner=request.user)

    if request.method == "POST":
        invoice.delete()
        return redirect("dashboard")

    return render(request, "delete_invoice.html", {"invoice": invoice})


# ======================
# PDF EXPORT
# ======================
@login_required
def invoice_pdf(request, invoice_id):
    """Render a printable, multi-page invoice for its owner."""
    invoice = get_object_or_404(
        Invoice.objects.prefetch_related("items"),
        pk=invoice_id, owner=request.user,
    )
    profile = CompanyProfile.objects.filter(user=request.user).first()
    response = HttpResponse(content_type="application/pdf")
    filename = slugify(invoice.invoice_number) or f"invoice-{invoice.pk}"
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    response["Cache-Control"] = "private, no-store"

    p = canvas.Canvas(response, pagesize=A4)
    width, height = A4
    ink = colors.HexColor("#12283c")
    muted = colors.HexColor("#61758a")
    accent = colors.HexColor("#087e80")
    light = colors.HexColor("#edf4f5")
    margin = 44
    p.setTitle(f"Invoice {invoice.invoice_number}")
    page_num = 1

    def text(x, y, value, size=10, bold=False, color=ink):
        p.setFillColor(color)
        p.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        p.drawString(x, y, str(value or ""))

    def page_footer():
        p.setStrokeColor(colors.HexColor("#d7e4e8"))
        p.line(margin, 46, width - margin, 46)
        text(margin, 30, "Generated with LedgerLite", 8, color=muted)
        p.setFont("Helvetica", 8)
        p.setFillColor(muted)
        p.drawRightString(width - margin, 30, f"Page {page_num}")

    def table_heading(y):
        p.setFillColor(ink)
        p.roundRect(margin, y - 16, width - 2 * margin, 30, 5, fill=1, stroke=0)
        text(margin + 10, y - 4, "DESCRIPTION", 9, True, colors.white)
        text(330, y - 4, "QTY", 9, True, colors.white)
        text(386, y - 4, "PRICE", 9, True, colors.white)
        text(475, y - 4, "AMOUNT", 9, True, colors.white)
        return y - 32

    p.setFillColor(light)
    p.roundRect(margin, height - 160, width - 2 * margin, 120, 12, stroke=0, fill=1)
    if profile and profile.company_logo:
        try:
            profile.company_logo.open("rb")
            logo_bytes = profile.company_logo.read()
            if len(logo_bytes) <= 4 * 1024 * 1024:
                p.drawImage(
                    ImageReader(BytesIO(logo_bytes)),
                    margin + 12, height - 138, width=62, height=62,
                    preserveAspectRatio=True, anchor="c", mask="auto",
                )
        except Exception:
            pass  # A missing or inaccessible remote logo must not prevent invoice export.
        finally:
            try:
                profile.company_logo.close()
            except Exception:
                pass

    company_name = (
        profile.company_name if profile and profile.company_name else "LedgerLite Business"
    )
    for i, part in enumerate(simpleSplit(company_name, "Helvetica-Bold", 13, 235)[:2]):
        text(126, height - 76 - 16 * i, part, 13, True)
    if profile:
        text(126, height - 116, (profile.company_email or "")[:39], 9, color=muted)
        text(126, height - 130, (profile.company_phone or "")[:39], 9, color=muted)
    p.setFillColor(accent)
    p.setFont("Helvetica-Bold", 22)
    p.drawRightString(width - margin - 13, height - 78, "INVOICE")

    y = height - 191
    text(margin, y, "BILL TO", 9, True, accent)
    text(margin, y - 20, invoice.customer_name or "Customer", 11, True)
    text(margin, y - 36, invoice.customer_phone, 9, color=muted)
    text(360, y, "INVOICE NUMBER", 9, True, accent)
    text(360, y - 20, invoice.invoice_number, 10, True)
    text(360, y - 36, timezone.localtime(invoice.date_created).strftime("%d %b %Y"), 9, color=muted)
    y = table_heading(y - 70)

    for item in invoice.items.all():
        parts = simpleSplit(item.description or "-", "Helvetica", 10, 265) or ["-"]
        row_height = max(34, len(parts) * 14 + 12)
        if y - row_height < 114:
            page_footer()
            p.showPage()
            page_num += 1
            text(margin, height - 54, f"Invoice {invoice.invoice_number} (continued)", 11, True)
            y = table_heading(height - 90)
        p.setStrokeColor(colors.HexColor("#e1e9ef"))
        p.line(margin, y - row_height + 6, width - margin, y - row_height + 6)
        for index, line in enumerate(parts):
            text(margin + 10, y - 13 - 14 * index, line)
        text(330, y - 13, item.quantity)
        text(386, y - 13, f"${item.price:,.2f}")
        text(475, y - 13, f"${item.subtotal:,.2f}", bold=True)
        y -= row_height

    if y < 160:
        page_footer()
        p.showPage()
        page_num += 1
        text(margin, height - 54, f"Invoice {invoice.invoice_number} (summary)", 11, True)
        y = height - 102
    subtotal = invoice.subtotal_amount
    p.setStrokeColor(colors.HexColor("#d7e4e8"))
    p.line(330, y - 6, width - margin, y - 6)
    text(335, y - 26, "Subtotal", color=muted)
    p.setFillColor(ink)
    p.setFont("Helvetica", 10)
    p.drawRightString(width - margin - 2, y - 26, f"${subtotal:,.2f}")
    text(335, y - 47, "Discount", color=muted)
    p.drawRightString(width - margin - 2, y - 47, f"-${invoice.discount:,.2f}")
    p.setFillColor(accent)
    p.setFont("Helvetica-Bold", 13)
    p.drawString(335, y - 75, "TOTAL")
    p.drawRightString(width - margin - 2, y - 75, f"${invoice.total:,.2f}")
    page_footer()
    p.save()
    return response


# ======================
# PROFILE
# ======================
@login_required
def profile(request):
    profile_obj, _ = CompanyProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        profile_obj.company_name = request.POST.get("company_name", "").strip()
        profile_obj.company_phone = request.POST.get("company_phone", "").strip()
        profile_obj.company_email = request.POST.get("company_email", "").strip()
        profile_obj.company_address = request.POST.get("company_address", "").strip()
        logo = request.FILES.get("company_logo")
        if logo:
            if logo.size > 2 * 1024 * 1024:
                messages.error(request, "Logo must be smaller than 2 MB.")
                return redirect("profile")
            profile_obj.company_logo = logo
        profile_obj.save()
        messages.success(request, "Business profile saved.")
        return redirect("profile")
    return render(request, "profile.html", {"profile": profile_obj})


# ======================


# ======================
# RESTOCK
# ======================
@login_required
def restock(request):
    products = Product.objects.filter(owner=request.user, is_deleted=False)
    if request.method == "POST":
        try:
            quantity = int(request.POST.get("quantity") or "0")
            if quantity <= 0 or quantity > 100000000:
                raise ValueError
        except ValueError:
            messages.error(request, "Restock quantity must be a positive whole number.")
            return redirect("restock")
        product = get_object_or_404(
            Product, pk=request.POST.get("product_id"),
            owner=request.user, is_deleted=False,
        )
        Product.objects.filter(pk=product.pk, owner=request.user).update(
            stock=F("stock") + quantity
        )
        Notification.objects.filter(
            user=request.user, type="stock", product_id=product.pk
        ).delete()
        messages.success(request, f"Restocked {product.name} by {quantity}.")
        return redirect("products")
    return render(request, "restock.html", {"products": products})


# ======================


# ======================
# PUBLIC INVOICE
# ======================
def public_invoice(request, token):
    invoice = get_object_or_404(Invoice, share_token=token)
    return render(request, "public_invoice.html", {"invoice": invoice})


# ======================
# ANALYTICS
# ======================
@login_required
def analytics(request):

    invoices = Invoice.objects.filter(owner=request.user)
    products = Product.objects.filter(owner=request.user, is_deleted=False)

    total_revenue = sum((Decimal(str(i.total or 0)) for i in invoices), Decimal("0.00"))

    total_invoices = invoices.count()

    average_invoice = (
        total_revenue / total_invoices if total_invoices else Decimal("0.00")
    )

    product_count = products.count()

    low_stock_count = products.filter(stock__gt=0, stock__lte=5).count()
    out_of_stock_count = products.filter(stock=0).count()

    top_products = (
        InvoiceItem.objects.filter(invoice__owner=request.user)
        .values("description")
        .annotate(total_sold=Sum("quantity"))
        .order_by("-total_sold")[:5]
    )

    monthly_revenue = (
        invoices
        .annotate(month=TruncMonth("date_created"))
        .values("month")
        .annotate(total=Sum("total"))
        .order_by("month")
    )

    chart_labels = []
    chart_values = []

    for m in monthly_revenue:
        chart_labels.append(m["month"].strftime("%b %Y") if m["month"] else "Unknown")
        chart_values.append(float(m["total"] or 0))

    insights = []

    if total_revenue >= 1000:
        insights.append(f"Revenue has reached ${total_revenue:.2f}")

    if low_stock_count:
        insights.append(f"{low_stock_count} products are running low")

    if out_of_stock_count:
        insights.append(f"{out_of_stock_count} products are out of stock")

    if total_invoices:
        insights.append(f"Average invoice value is ${average_invoice:.2f}")

    return render(request, "analytics.html", {
        "total_revenue": total_revenue,
        "total_invoices": total_invoices,
        "average_invoice": average_invoice,
        "product_count": product_count,
        "low_stock_count": low_stock_count,
        "out_of_stock_count": out_of_stock_count,
        "top_products": top_products,
        "insights": insights,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
    })


# ======================
# DELETE PRODUCT
# ======================
@login_required
def delete_product(request, product_id):

    product = get_object_or_404(Product, id=product_id, owner=request.user)

    if request.method == "POST":
        product.is_deleted = True
        product.save()
        messages.warning(request, "Product moved to trash.")
        return redirect("products")

    return render(request, "delete_product.html", {"product": product})


@login_required
def restore_product(request, product_id):

    product = get_object_or_404(Product, id=product_id, owner=request.user)

    product.is_deleted = False
    product.save()

    messages.success(request, "Product restored.")
    return redirect("products")


# ======================
# STOCK NOTIFICATIONS ENGINE
# ======================
def generate_stock_notifications(user):
    products = Product.objects.filter(owner=user)

    for p in products:
        if p.stock <= 5:
            exists = Notification.objects.filter(
                user=user,
                type="stock",
                message__icontains=p.name
            ).exists()

            if not exists:
                Notification.objects.create(
                    user=user,
                    title="Low Stock Alert",
                    message=f"{p.name} is running low ({p.stock} left)",
                    type="stock"
                )


# ======================
# STOCK CHECK ENGINE (FIXED)
# ======================
def run_stock_check(user):
    products = Product.objects.filter(owner=user, is_deleted=False)

    for p in products:

        stock_notifications = Notification.objects.filter(
            user=user,
            type="stock",
            product_id=p.id
        )

        if p.stock <= 5:

            message = f"{p.name} is running low ({p.stock} left)"

            if stock_notifications.exists():
                stock_notifications.update(
                    message=message,
                    read=False
                )
            else:
                Notification.objects.create(
                    user=user,
                    title="Low Stock Alert",
                    message=message,
                    type="stock",
                    product_id=p.id
                )
        else:
            stock_notifications.delete()


# -------------------------
# API
# -------------------------
@login_required
def notifications_api(request):
    notifications = Notification.objects.filter(user=request.user).order_by("-created_at")[:20]

    data = [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "type": n.type,
            "read": n.read,
            "resolved": n.resolved,
            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for n in notifications
    ]

    return JsonResponse({
        "notifications": data,
        "unread_count": Notification.objects.filter(user=request.user, read=False).count()
    })


# -------------------------
# MARK AS READ (OK)
# -------------------------
@login_required
@require_POST
def notification_ok(request, notification_id):
    n = get_object_or_404(Notification, id=notification_id, user=request.user)
    n.read = True
    n.save()
    return JsonResponse({"success": True})


# -------------------------
# RESOLVE
# -------------------------
@login_required
@require_POST
def notification_resolve(request, notification_id):
    try:
        n = get_object_or_404(Notification, id=notification_id, user=request.user)

        n.read = True
        n.resolved = True
        n.save()

        redirect_url = reverse("products")

        if n.product:
            redirect_url = f"{redirect_url}#product-{n.product.id}"

        return JsonResponse({
            "success": True,
            "redirect_url": redirect_url
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)


# -------------------------
# DELETE
# -------------------------
@login_required
@require_POST
def notification_delete(request, notification_id):
    n = get_object_or_404(Notification, id=notification_id, user=request.user)
    n.delete()
    return JsonResponse({"success": True})


# -------------------------
# CLEAR ALL (FIXED NAME)
# -------------------------
@login_required
@require_POST
def notifications_clear(request):
    Notification.objects.filter(user=request.user).delete()
    return JsonResponse({"success": True})


# -------------------------
# MARK ALL READ
# -------------------------
@login_required
@require_POST
def mark_all_notifications_read(request):
    Notification.objects.filter(user=request.user, read=False).update(read=True)
    return JsonResponse({"success": True})


# ALL INVOICES PAGE (FIXED)
# -------------------------
@login_required
def all_invoices(request):
    invoices = Invoice.objects.filter(owner=request.user).order_by("-date_created")
    return render(request, "all_invoices.html", {"invoices": invoices})


@login_required
def export_invoices_csv(request):
    """Download this account's invoices only; no shared or cross-tenant rows."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="ledgerlite-invoices.csv"'
    response["Cache-Control"] = "no-store"
    writer = csv.writer(response)
    writer.writerow(["Invoice", "Customer", "Phone", "Created", "Subtotal", "Discount", "Total"])
    for invoice in (
        Invoice.objects.filter(owner=request.user)
        .prefetch_related("items").order_by("-date_created")
    ):
        # Prevent spreadsheet formula injection in user-provided fields.
        def safe_cell(value):
            text = str(value or "")
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
        writer.writerow([
            invoice.invoice_number, safe_cell(invoice.customer_name),
            safe_cell(invoice.customer_phone), invoice.date_created.isoformat(),
            invoice.subtotal_amount, invoice.discount, invoice.total,
        ])
    return response


@login_required
def dashboard(request):

    run_stock_check(request.user)

    invoices = Invoice.objects.filter(owner=request.user).order_by("-id")

    notifications = Notification.objects.filter(
        user=request.user
    ).order_by("-id")[:10]

    unread_count = Notification.objects.filter(
        user=request.user,
        read=False
    ).count()

    total_revenue = Decimal("0.00")
    total_discount = Decimal("0.00")

    for inv in invoices:
        try:
            total_revenue += Decimal(str(inv.total or "0"))
            total_discount += Decimal(str(inv.discount or "0"))
        except:
            continue

    # ✅ ADD THIS (CRITICAL FIX)
    products = Product.objects.filter(owner=request.user, is_deleted=False)
    low_stock_count = products.filter(stock__gt=0, stock__lte=5).count()

    return render(request, "dashboard.html", {
        "invoices": invoices[:5],
        "invoice_count": invoices.count(),
        "total_revenue": total_revenue,
        "total_discount": total_discount,
        "notifications": notifications,
        "unread_count": unread_count,

        # ✅ FIXED VALUE
        "low_stock_count": low_stock_count,
    })


def terms(request):
    return render(request, 'terms.html')


def privacy(request):
    return render(request, 'privacy.html')


@login_required
def support(request):

    if request.method == "POST":

        form = SupportTicketForm(request.POST)

        if form.is_valid():

            ticket = form.save(commit=False)

            if request.user.is_authenticated:
                ticket.user = request.user

            ticket.save()

            messages.success(
                request,
                "Support ticket submitted successfully."
            )

            return redirect('support')

    else:

        form = SupportTicketForm()

    return render(
        request,
        'support.html',
        {'form': form}
    )



