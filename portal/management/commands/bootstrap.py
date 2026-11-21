"""Idempotent first-run setup, safe to run on every container start."""
import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.models import Batch, Milestone, SiteConfig


class Command(BaseCommand):
    help = "Create default settings, a first milestone, placeholder batches and (optionally) an admin user."

    def handle(self, *args, **options):
        SiteConfig.get()

        if not Milestone.objects.exists():
            deadline = (timezone.localtime() + timedelta(days=14)).replace(hour=23, minute=59, second=0, microsecond=0)
            Milestone.objects.create(
                name="Milestone 2 Report", slug="milestone-2", deadline=deadline,
                description="Upload one report per team (PDF or Word).",
            )
            self.stdout.write("Created default milestone (deadline in 14 days — change it in the admin).")

        if not Batch.objects.exists():
            for n in (1, 2, 3):
                Batch.objects.create(name=f"Batch {n}", max_team_size=5)
            self.stdout.write("Created placeholder batches — rename them in the admin.")

        username, password = os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_PASSWORD")
        User = get_user_model()
        if username and password and not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser(username, os.environ.get("ADMIN_EMAIL", ""), password)
            self.stdout.write(f"Created admin user '{username}'.")
