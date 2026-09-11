from .import_place_decennial_reference import Command as PopulationImportCommand


class Command(PopulationImportCommand):
    help = 'Import validated Ohio ZCTA race/sex/age populations for 2010 and 2020.'
    default_geography = 'zcta'
