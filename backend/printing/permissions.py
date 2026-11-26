from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.models import User

Role = User.Role


class CanUsePrinterSettings(BasePermission):
    """Admin reads and changes the printer settings. Kitchen may only READ them: the
    local print.py bridge normally logs in with a Kitchen account to fetch the folder."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return user.role in (Role.ADMIN, Role.KITCHEN)
        return user.role == Role.ADMIN
