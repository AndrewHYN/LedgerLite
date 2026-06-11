from django import forms
from .models import SupportTicket

class SupportTicketForm(forms.ModelForm):

    class Meta:
        model = SupportTicket

        fields = [
            'ticket_type',
            'priority',
            'subject',
            'message'
        ]

        widgets = {
            'ticket_type': forms.Select(attrs={
                'class': 'form-select shadow-sm'
            }),

            'priority': forms.Select(attrs={
                'class': 'form-select shadow-sm'
            }),

            'subject': forms.TextInput(attrs={
                'class': 'form-control shadow-sm',
                'placeholder': 'Enter subject...'
            }),

            'message': forms.Textarea(attrs={
                'class': 'form-control shadow-sm',
                'rows': 6,
                'placeholder': 'Describe your issue in detail...'
            }),
        }