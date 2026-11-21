"""Consistent SQLite backup (safe while the site is running). Keeps the newest 14 copies."""
import sqlite3
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Write a timestamped copy of the database to DATA_DIR/backups/."

    def handle(self, *args, **options):
        target_dir = settings.DATA_DIR / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"db-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
        connection.ensure_connection()
        dest = sqlite3.connect(target)
        try:
            connection.connection.backup(dest)
        finally:
            dest.close()
        for old in sorted(target_dir.glob("db-*.sqlite3"))[:-14]:
            old.unlink()
        self.stdout.write(f"Backup written to {target}")
