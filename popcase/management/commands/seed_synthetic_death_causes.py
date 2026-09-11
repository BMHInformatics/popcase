"""Explicit, repeatable preparation of the SDD synthetic mortality fixture."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Q
from popcase.models import NaaccrData


class Command(BaseCommand):
    help = 'Copy primary sites into missing/0000 causes of death in synthetic data only.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Apply changes; otherwise only report the count.')

    def handle(self, *args, **options):
        if not settings.MORTALITY_SYNTHETIC_PRIMARY_SITE_CAUSES:
            raise CommandError('Enable MORTALITY_SYNTHETIC_PRIMARY_SITE_CAUSES only for the synthetic registry first.')
        rows = NaaccrData.objects.filter(
            Q(cause_of_death__isnull=True) | Q(cause_of_death__regex=r'^\s*(0000)?\s*$'),
            primary_site__regex=r'^C[0-9]{3}$',
        )
        if not options['apply']:
            self.stdout.write(f'{rows.count()} synthetic rows eligible; use --apply to update.')
            return
        with transaction.atomic(using=rows.db):
            updated = rows.update(cause_of_death=F('primary_site'))
        self.stdout.write(self.style.SUCCESS(f'Updated {updated} synthetic causes of death.'))
