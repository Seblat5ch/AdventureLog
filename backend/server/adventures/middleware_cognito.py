"""
Cognito ALB Authentication Middleware

When an ALB has Cognito authentication enabled, it passes user info
in x-amzn-oidc-* headers after successful authentication.

This middleware reads those headers and auto-creates/logs in the
corresponding Django user, so the app never shows its own login page.
"""

import base64
import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login

logger = logging.getLogger(__name__)
User = get_user_model()


class CognitoAlbAuthMiddleware:
    """
    Reads ALB Cognito headers and transparently logs the user into Django.
    
    ALB sets these headers after Cognito auth:
    - x-amzn-oidc-accesstoken: The raw access token
    - x-amzn-oidc-identity: The Cognito sub (user ID)
    - x-amzn-oidc-data: Base64-encoded JWT with user claims (email, name, etc.)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip if user is already authenticated
        if request.user and request.user.is_authenticated:
            return self.get_response(request)

        # Check for ALB Cognito headers
        oidc_data = request.META.get('HTTP_X_AMZN_OIDC_DATA')
        if not oidc_data:
            return self.get_response(request)

        try:
            # The OIDC data is a JWT — we only need the payload (middle part)
            # ALB has already validated it, so we trust it
            parts = oidc_data.split('.')
            if len(parts) != 3:
                return self.get_response(request)

            # Decode the payload (add padding if needed)
            payload = parts[1]
            payload += '=' * (4 - len(payload) % 4)
            claims = json.loads(base64.b64decode(payload))

            email = claims.get('email', '')
            username = claims.get('cognito:username', '') or claims.get('sub', '')
            
            if not email and not username:
                return self.get_response(request)

            # Find or create the Django user
            user = self._get_or_create_user(email, username, claims)
            if user:
                # Log the user in without requiring a password
                login(request, user, backend='users.backends.NoPasswordAuthBackend')

        except Exception as e:
            logger.warning(f'Cognito ALB auth middleware error: {e}')

        return self.get_response(request)

    def _get_or_create_user(self, email: str, username: str, claims: dict):
        """Find existing user by email or create a new one."""
        # Try to find by email first
        if email:
            try:
                return User.objects.get(email=email)
            except User.DoesNotExist:
                pass

        # Try by username
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                pass

        # Auto-create the user
        if email:
            safe_username = email.split('@')[0]
            # Ensure unique username
            base_username = safe_username
            counter = 1
            while User.objects.filter(username=safe_username).exists():
                safe_username = f'{base_username}{counter}'
                counter += 1

            # First Cognito user gets superuser/staff privileges
            # (the entrypoint creates a bootstrap 'admin' user, so check for real users instead)
            real_users = User.objects.exclude(email='admin@example.com').count()
            is_first_real_user = real_users == 0

            user = User.objects.create_user(
                username=safe_username,
                email=email,
                first_name=claims.get('given_name', ''),
                last_name=claims.get('family_name', ''),
            )
            user.set_unusable_password()
            if is_first_real_user:
                user.is_staff = True
                user.is_superuser = True
            user.save()
            logger.info(f'Auto-created Django user {safe_username} from Cognito')
            return user

        return None
