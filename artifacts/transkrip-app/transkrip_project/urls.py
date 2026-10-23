from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.views.decorators.csrf import csrf_exempt

urlpatterns = [
    path('admin/', admin.site.urls),
    # csrf_exempt pada login aman untuk internal tool:
    # risiko "login CSRF" tidak relevan karena tidak ada akun publik.
    path('login/', csrf_exempt(auth_views.LoginView.as_view(template_name='login.html')), name='login'),
    path('logout/', csrf_exempt(auth_views.LogoutView.as_view()), name='logout'),
    path('', include('core.urls')),
    path('ecampuz/', include('dashboard.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
