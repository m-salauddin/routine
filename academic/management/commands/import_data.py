import os
from django.core.management.base import BaseCommand
from academic.services.excel_importer import ExcelImporter

class Command(BaseCommand):
    help = 'Imports or updates academic data from the master Excel file securely.'

    def add_arguments(self, parser):
        # Allow the user to specify a custom file path if needed
        parser.add_argument(
            '--file', 
            type=str, 
            default='academic_data.xlsx',
            help='Path to the master Excel file (default: academic_data.xlsx in root)'
        )

    def handle(self, *args, **options):
        file_name = options['file']
        
        # Resolve the absolute path of the file
        base_dir = os.getcwd()
        file_path = os.path.join(base_dir, file_name)

        self.stdout.write(self.style.WARNING(f"Reading data from: {file_path}..."))

        # Call our service to do the heavy lifting
        success, message = ExcelImporter.import_data(file_path)

        if success:
            self.stdout.write(self.style.SUCCESS(f"✅ {message}"))
        else:
            self.stdout.write(self.style.ERROR(f"❌ {message}"))