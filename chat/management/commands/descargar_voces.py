import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Descarga las voces locales de Piper (español, inglés y portugués) en voices/."

    def handle(self, *args, **options):
        settings.PIPER_VOICES_DIR.mkdir(exist_ok=True)
        names = sorted(set(settings.PIPER_VOICES.values()))
        subprocess.run(
            [sys.executable, "-m", "piper.download_voices", "--download-dir", str(settings.PIPER_VOICES_DIR), *names],
            check=True,
        )
        self.stdout.write(self.style.SUCCESS(f"Voces listas en {settings.PIPER_VOICES_DIR}: {', '.join(names)}"))
