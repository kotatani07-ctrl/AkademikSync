from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('generate/single/', views.generate_single, name='generate_single'),
    path('generate/batch/', views.generate_batch, name='generate_batch'),
    path('generate/batch/<uuid:batch_id>/cancel/', views.cancel_generate_batch, name='cancel_generate_batch'),
    path('riwayat/', views.riwayat, name='riwayat'),
]
