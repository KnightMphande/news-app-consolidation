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
    """Synchronise the user's role group after a normal save.

    Skip raw saves during fixture loading, when related records may not
    be ready to update. Run ``manage.py setup_roles`` after the import
    to reconcile group membership.

    :param sender: The User model that sent the signal.
    :param instance: The saved user to synchronise.
    :param raw: Whether Django saved the user as part of a fixture load.
    :param kwargs: Additional metadata supplied by Django's signal.
    """    # Fixture loading should not trigger changes to related records.
    if raw:
        return

    sync_user_role(instance)
