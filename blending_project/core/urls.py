
from django.urls import path
from .views import TankListView, CalculateBlendView

urlpatterns = [
    path("tanks/", TankListView.as_view(), name="tank-list"),
    path("calculate/", CalculateBlendView.as_view(), name="calculate-blend"),
]
