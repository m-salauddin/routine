"""
URL configuration for Class_Routine project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from user_api.views import first_page
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

# Swagger-এর জন্য নতুন ইমপোর্টগুলো
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

# Swagger কনফিগারেশন
schema_view = get_schema_view(
   openapi.Info(
      title="Routine Generator API",
      default_version='v1',
      description="API documentation for Automated Class Scheduling System",
   ),
   public=True,
   permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    path('admin/', admin.site.urls),
    
    path('', first_page),
    
    path('api/', include('user_api.urls')), 
    
    path('api/academic/', include('academic.urls')), 
    
    # Swagger-এর URL নতুন যুক্ত করা হলো
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
]

urlpatterns += staticfiles_urlpatterns()