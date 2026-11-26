from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.models import User

Role = User.Role


class CanUseCalls(BasePermission):
    """RBAC matrix, "KDS Queue & Call Waiter": reading calls is for Admin (everything of
    the restaurant), Staff (the alerts they can act on) and Kitchen (their own calls);
    creating a call here is Kitchen only (guests use the public customer route)."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return user.role in (Role.ADMIN, Role.STAFF, Role.KITCHEN)
        return user.role == Role.KITCHEN


class CanResolveCall(BasePermission):
    """Staff resolve the calls they picked; the restaurant Admin may also close a picked
    call on the waiter's behalf. Which call each may touch is decided in `CallService`."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in (Role.STAFF, Role.ADMIN))


class IsCallAdmin(BasePermission):
    """Only the restaurant Admin cancels calls."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.ADMIN)


class IsWaiter(BasePermission):
    """Only Staff (waiters) pick and resolve calls. Whether *this* waiter may touch *this*
    call (assignment) is decided in `CallService`."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.STAFF)
