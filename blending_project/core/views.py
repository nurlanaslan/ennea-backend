
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .services.excel import get_available_tanks, get_target_specs
from .services.blending import BlendingOptimizer

class TankListView(APIView):
    def get(self, request):
        tanks = get_available_tanks()
        specs = get_target_specs()
        return Response({
            "tanks": tanks,
            "specs": specs
        })

class CalculateBlendView(APIView):
    def post(self, request):
        try:
            qty = float(request.data.get('quantity', 0))
            if qty <= 0:
                return Response({"error": "Quantity must be positive"}, status=status.HTTP_400_BAD_REQUEST)
            
            tanks = get_available_tanks()
            specs = get_target_specs()
            
            optimizer = BlendingOptimizer(tanks, specs, qty)
            result = optimizer.optimize()
            
            if not result['success']:
                return Response({
                    "success": False,
                    "message": "Optimization failed. Likely infeasible constraints.",
                    "details": result['status']
                })
            
            # Format results
            amounts = result['amounts']
            allocation = []
            total_mass = 0
            
            for i, tank in enumerate(tanks):
                amount = amounts[i]
                if amount > 0.001: # Filter out tiny amounts
                    allocation.append({
                        "tank_id": tank['id'],
                        "tank_name": tank['name'],
                        "amount": round(amount, 3),
                        "properties": tank['properties']
                    })
                    total_mass += amount
            
            return Response({
                "success": True,
                "allocation": allocation,
                "total_mass": total_mass,
                "target_specs": specs
            })

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
