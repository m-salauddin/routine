# user_api/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.hashers import make_password
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from .models import User

# ==============================================================================
# IMPORT/EXPORT RESOURCE (The Magic Hook for Auto Password Hashing)
# ==============================================================================
class UserResource(resources.ModelResource):
    class Meta:
        model = User
        import_id_fields = ('username',) 
        skip_unchanged = True
        report_skipped = True
       
        fields = ('id', 'username', 'email', 'password', 'first_name', 'last_name', 'role', 'department', 'batch', 'semester', 'is_active', 'is_staff', 'is_superuser')

    def before_import_row(self, row, **kwargs):
        """password field is hashed before saving to the database."""
        password = row.get('password')
        if password:
            if not str(password).startswith('pbkdf2_'):
                row['password'] = make_password(str(password))
        else:
            if 'password' in row:
                del row['password']

# ==============================================================================
# ADMIN CLASS (Merging Custom Fields with Django's Built-in UserAdmin)
# ==============================================================================
@admin.register(User)
class CustomUserAdmin(ImportExportModelAdmin, UserAdmin):
    resource_class = UserResource
    
    # Adding the custom fields to the list display, filter, and search options
    list_display = ('username', 'email', 'first_name', 'last_name', 'role', 'department', 'batch', 'semester', 'is_active')
    list_filter = ('role', 'department', 'batch', 'semester', 'is_active', 'is_staff')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    
    # manually adding the custom fields to the fieldsets of the UserAdmin
    fieldsets = UserAdmin.fieldsets + (
        ('Academic Profile (Custom Fields)', {
            'fields': ('role', 'department', 'batch', 'semester')
        }),
    )