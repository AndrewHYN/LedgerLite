"""WSGI entrypoint for LedgerLite."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "invoicepro.settings")
application = get_wsgi_application()

if os.environ.get("RENDER") == "true":
    import invoicepro.auto_migrate

# Temporary friend preview: no Render database or real customer accounts are used.
# Each Vercel function instance starts with disposable demonstration data.
if os.environ.get("LEDGERLITE_FRIEND_DEMO") == "1":
    from django.core.management import call_command
    from django.contrib.auth import get_user_model

    call_command("migrate", interactive=False, verbosity=0, run_syncdb=True)
    demo_username = os.environ.get("LEDGERLITE_DEMO_USER", "ledgerlite-demo")
    demo_password = os.environ.get("LEDGERLITE_DEMO_PASSWORD")
    if demo_password:
        user, created = get_user_model().objects.get_or_create(username=demo_username)
        if created:
            user.set_password(demo_password)
            user.save(update_fields=["password"])
