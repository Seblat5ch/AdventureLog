"""
SSO Login endpoint — the Cognito middleware runs on this request,
creates/logs in the user, and we return the session + user info.
No authentication required (the middleware handles it).
"""
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


@method_decorator(csrf_exempt, name='dispatch')
class SsoLoginView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        if request.user and request.user.is_authenticated:
            return Response({
                'username': request.user.username,
                'email': request.user.email or '',
                'first_name': request.user.first_name or '',
                'last_name': request.user.last_name or '',
                'is_staff': request.user.is_staff,
            })
        return Response({'error': 'Not authenticated'}, status=401)
