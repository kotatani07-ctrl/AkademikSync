# pyrefly: ignore[missing-import]
from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('generate/single/', views.generate_single, name='generate_single'),
    path('generate/single/preview/', views.preview_single, name='preview_single'),
    path('generate/batch/', views.generate_batch, name='generate_batch'),
    path('generate/batch/<uuid:batch_id>/status/', views.batch_status, name='batch_status'),
    path('generate/batch/<uuid:batch_id>/download/', views.batch_download, name='batch_download'),
    path('generate/batch/<uuid:batch_id>/cancel/', views.cancel_generate_batch, name='cancel_generate_batch'),
    path('riwayat/', views.riwayat, name='riwayat'),
]
