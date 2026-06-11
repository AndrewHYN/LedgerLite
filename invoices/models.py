from decimal import Decimal
import uuid

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.db import models
from django.contrib.auth.models import User


# ======================
# COMPANY PROFILE
# ======================
class CompanyProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    company_name = models.CharField(max_length=255, blank=True, null=True)
    company_phone = models.CharField(max_length=50, blank=True, null=True)
    company_email = models.EmailField(blank=True, null=True)
    company_address = models.TextField(blank=True, null=True)

    company_logo = models.ImageField(
        upload_to="logos/",
        blank=True,
        null=True
    )

    def __str__(self):
        return self.company_name or self.user.username


# ======================
# PRODUCT
# ======================
class Product(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)

    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    stock = models.IntegerField(default=0)

    created_at = models.DateTimeField(default=timezone.now)
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("product_detail", args=[self.id])


# ======================
# INVOICE
# ======================
class Invoice(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)

    invoice_number = models.CharField(max_length=50, unique=True)

    customer_name = models.CharField(max_length=255, blank=True)
    customer_phone = models.CharField(max_length=50, blank=True)

    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    # FIXED (safe datetime handling)
    date_created = models.DateTimeField(default=timezone.now)

    # PUBLIC SHARING LINK
    share_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    def update_total(self):
        total = Decimal("0.00")

        for item in self.items.all():
            total += item.quantity * item.price

        total -= self.discount
        self.total = max(total, Decimal("0.00"))

        self.save(update_fields=["total"])

    def __str__(self):
        return self.invoice_number


# ======================
# INVOICE ITEM
# ======================
class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        related_name="items",
        on_delete=models.CASCADE
    )

    description = models.CharField(max_length=255)

    quantity = models.IntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        return f"{self.description} ({self.quantity})"


# ======================
# NOTIFICATION SYSTEM
# ======================
class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    title = models.CharField(max_length=255)
    message = models.TextField()
    type = models.CharField(max_length=50, default="general")

    read = models.BooleanField(default=False)
    resolved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    
# ======================
# SUPPORT SYSTEM
# ======================

class SupportTicket(models.Model):

    TYPE_CHOICES = [
        ('support', 'Support'),
        ('bug', 'Bug Report'),
        ('feature', 'Feature Request'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]

    STATUS_CHOICES = [
        ('open', 'Open'),
        ('progress', 'In Progress'),
        ('resolved', 'Resolved'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    ticket_type = models.CharField(max_length=20, choices=TYPE_CHOICES)

    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default='medium'
    )

    subject = models.CharField(max_length=200)

    message = models.TextField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='open'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.ticket_type} - {self.subject}"