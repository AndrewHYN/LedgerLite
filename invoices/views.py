from decimal import Decimal, InvalidOperation

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
        password = request.POST.get("password", "").strip()

        if not username or not password:
            messages.error(request, "Username and password required.")
            return redirect("register")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("register")

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password
        )

        CompanyProfile.objects.get_or_create(user=user)

        login(request, user)
        return redirect("dashboard")


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

    low_stock_products = products.filter(stock__lte=5)

    print("LOW STOCK PRODUCTS:")
    for p in low_stock_products:
        print(p.name, p.stock)

    low_stock_count = low_stock_products.count()

    return render(request, "products.html", {
        "products": products,
        "low_stock_count": low_stock_count,
    })


# ======================
# ADD PRODUCT
# ======================
@login_required
def add_product(request):

    if request.method == "POST":

        name = request.POST.get("name", "").strip()

        try:
            price = Decimal(request.POST.get("price", "0"))
        except InvalidOperation:
            price = Decimal("0.00")

        try:
            stock = int(request.POST.get("stock", 0))
        except ValueError:
            stock = 0

        Product.objects.create(
            owner=request.user,
            name=name,
            price=price,
            stock=stock
        )

        return redirect("products")

    return render(request, "add_product.html")


# ======================
# CREATE INVOICE
# ======================
@login_required
def create_invoice(request):

    run_stock_check(request.user)

    products = Product.objects.filter(owner=request.user, is_deleted=False)

    if request.method == "POST":

        product_ids = request.POST.getlist("product_id[]")
        quantities = request.POST.getlist("quantity[]")

        if not product_ids:
            messages.error(request, "Add at least one product.")
            return redirect("create_invoice")

        try:
            discount = Decimal(request.POST.get("discount", "0"))
        except InvalidOperation:
            discount = Decimal("0.00")

        invoice = Invoice.objects.create(
            owner=request.user,
            invoice_number="TEMP",
            customer_name=request.POST.get("customer_name", "").strip(),
            customer_phone=request.POST.get("customer_phone", "").strip(),
            discount=discount
        )

        created = 0

        for pid, qty in zip(product_ids, quantities):

            try:
                product = Product.objects.get(id=pid, owner=request.user)
                qty = int(qty)

                if qty <= 0:
                    continue

                if product.stock < qty:
                    messages.warning(request, f"Not enough stock for {product.name}")
                    continue

                InvoiceItem.objects.create(
                    invoice=invoice,
                    description=product.name,
                    quantity=qty,
                    price=product.price
                )

                product.stock -= qty
                product.save()

                created += 1

            except Exception:
                continue

        if created == 0:
            invoice.delete()
            messages.error(request, "Invoice cannot be empty.")
            return redirect("create_invoice")

        invoice.update_total()
        invoice.invoice_number = f"INV-{invoice.id:05d}"
        invoice.save()

        messages.success(request, "Invoice created successfully.")
        return redirect("dashboard")

    return render(request, "create_invoice.html", {
        "products": products
    })


# ======================
# EDIT INVOICE
# ======================
@login_required
def edit_invoice(request, invoice_id):

    run_stock_check(request.user)

    invoice = get_object_or_404(Invoice, id=invoice_id, owner=request.user)

    if request.method == "POST":

        invoice.customer_name = request.POST.get("customer_name", "").strip()
        invoice.customer_phone = request.POST.get("customer_phone", "").strip()

        try:
            invoice.discount = Decimal(request.POST.get("discount", "0"))
        except InvalidOperation:
            invoice.discount = Decimal("0.00")

        invoice.save()

        item_ids = request.POST.getlist("item_id[]")
        quantities = request.POST.getlist("quantity[]")
        prices = request.POST.getlist("price[]")

        for item_id, qty, price in zip(item_ids, quantities, prices):
            try:
                item = InvoiceItem.objects.get(id=item_id, invoice=invoice)
                item.quantity = int(qty or 0)
                item.price = Decimal(price or "0")
                item.save()
            except:
                continue

        invoice.update_total()

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

    invoice = get_object_or_404(Invoice, id=invoice_id, owner=request.user)

    profile = CompanyProfile.objects.filter(user=request.user).first()

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="invoice_{invoice.invoice_number}.pdf"'

    p = canvas.Canvas(response)

    width, height = 595, 842
    y = height - 50

    header_height = 90

    p.setStrokeColorRGB(0.1, 0.1, 0.1)
    p.setLineWidth(1)
    p.rect(30, y - header_height, width - 60, header_height, stroke=1, fill=0)

    logo_x = 40
    logo_y = y - 70

    if profile and profile.company_logo:
        try:
            p.drawImage(
                profile.company_logo.path,
                logo_x,
                logo_y,
                width=60,
                height=60,
                preserveAspectRatio=True,
                mask='auto'
            )
        except:
            p.setFont("Helvetica-Bold", 10)
            p.drawString(logo_x, logo_y + 20, "LOGO")

    text_x = 120
    text_y = y - 35

    company_name = profile.company_name if profile else "LedgerLite Business"

    p.setFont("Helvetica-Bold", 14)
    p.drawString(text_x, text_y, company_name)

    p.setFont("Helvetica", 9)

    if profile:
        p.drawString(text_x, text_y - 15, f"Email: {profile.company_email or '-'}")
        p.drawString(text_x, text_y - 27, f"Phone: {profile.company_phone or '-'}")

    p.setFont("Helvetica-Bold", 22)
    p.drawRightString(width - 50, y - 40, "INVOICE")

    y -= 120

    p.setFont("Helvetica-Bold", 11)
    p.drawString(40, y, "Bill To:")

    y -= 15
    p.setFont("Helvetica", 10)
    p.drawString(40, y, f"{invoice.customer_name}")

    y -= 15
    p.drawString(40, y, f"Phone: {invoice.customer_phone}")

    y -= 25

    p.line(40, y, width - 40, y)
    y -= 25

    p.setFont("Helvetica-Bold", 11)
    p.drawString(40, y, "Item")
    p.drawString(300, y, "Qty")
    p.drawString(370, y, "Price")
    p.drawString(460, y, "Total")

    y -= 20

    subtotal = Decimal("0.00")

    for item in invoice.items.all():

        line_total = item.quantity * item.price
        subtotal += line_total

        p.drawString(40, y, str(item.description))
        p.drawString(300, y, str(item.quantity))
        p.drawString(370, y, f"${item.price}")
        p.drawString(460, y, f"${line_total}")

        y -= 18

    p.setFont("Helvetica-Bold", 11)
    p.drawString(320, y - 20, "TOTAL:")
    p.drawString(460, y - 20, f"${invoice.total}")

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

        if request.FILES.get("company_logo"):
            profile_obj.company_logo = request.FILES["company_logo"]

        profile_obj.save()
        return redirect("profile")

    return render(request, "profile.html", {"profile": profile_obj})


# ======================
# RESTOCK
# ======================
@login_required
def restock(request):

    products = Product.objects.filter(owner=request.user, is_deleted=False)

    if request.method == "POST":

        product_id = request.POST.get("product_id")
        qty = int(request.POST.get("quantity") or 0)

        product = get_object_or_404(Product, id=product_id, owner=request.user)

        if qty > 0:
            product.stock += qty
            product.save()

            Notification.objects.filter(
                user=request.user,
                type="stock",
                message__icontains=product.name
            ).delete()

        return redirect("products")

    return render(request, "restock.html", {"products": products})


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

    low_stock_count = products.filter(stock__lte=5).count()
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
    invoices = Invoice.objects.all().order_by("-date_created")
    return render(request, "all_invoices.html", {"invoices": invoices})


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
    low_stock_count = products.filter(stock__lte=5).count()

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


@login_required
def terms(request):
    return render(request, 'terms.html')


@login_required
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