from django.contrib import admin
from .models import Invoice, InvoiceItem

admin.site.register(Invoice)
admin.site.register(InvoiceItem)

from django.contrib import admin
from .models import SupportTicket

@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):

    list_display = (
        'subject',
        'ticket_type',
        'priority',
        'status',
        'created_at'
    )

    list_filter = (
        'ticket_type',
        'priority',
        'status'
    )

    search_fields = (
        'subject',
        'message'
    )