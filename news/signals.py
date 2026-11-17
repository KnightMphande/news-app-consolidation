"""Synchronise user roles on save; article publication does not use signals."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User
from .roles import sync_user_role


@receiver(
    post_save,
    sender=User,
    dispatch_uid="news.sync_user_role",
)
def assign_user_role_group(sender, instance, raw=False, **kwargs):
    """Assign the correct role group after creating or updating a user."""
    # Fixture loading should not trigger changes to related records.
    if raw:
        return

    sync_user_role(instance)
