from django.urls import path
from invoices import views

# Legacy /accounts/register/ shares the validated registration flow.
urlpatterns = [path("register/", views.register, name="accounts_register")]
