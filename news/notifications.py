"""Subscriber delivery and signed internal API integration with bounded timeouts."""

import logging
from smtplib import SMTPException

import requests
from django.conf import settings
from django.core import signing
from django.core.mail import send_mass_mail
from django.db.models import Q

from .models import User

logger = logging.getLogger(__name__)

# Separate these signatures from other signed data in the application.
APPROVAL_SIGNING_SALT = "news.article-approval"


def notify_article_approval(article):
    """Email subscribers and notify the internal API after approval.

    Return any delivery warnings so the editor can see partial failures.
    The article remains approved if a notification service fails.
    """
    warnings = []

    subscription_filter = Q(subscribed_journalists=article.author)

    if article.publisher_id:
        subscription_filter |= Q(subscribed_publishers=article.publisher)

    subscribers = (
        User.objects.filter(
            subscription_filter,
            role=User.Role.READER,
            is_active=True,
        )
        .exclude(email="")
        .distinct()
    )

    # Deduplicate addresses as well as user accounts.
    recipients = sorted(
        {
            email.strip().lower()
            for email in subscribers.values_list("email", flat=True)
            if email.strip()
        }
    )

    subject = f"New article: {article.title}"
    body = (
        f"{article.title}\n\n"
        f"By {article.author.username}\n\n"
        f"{article.content}"
    )

    # Separate messages prevent subscribers seeing each other's addresses.
    messages = [
        (subject, body, settings.DEFAULT_FROM_EMAIL, [email])
        for email in recipients
    ]

    try:
        if messages:
            send_mass_mail(messages, using="default")
    except (SMTPException, OSError):
        logger.exception(
            "Subscriber email failed for article %s.",
            article.pk,
        )
        warnings.append("Subscriber email delivery failed.")

    # Sign the article ID so arbitrary callers cannot create approval logs.
    event = signing.dumps(
        {"article_id": article.pk},
        salt=APPROVAL_SIGNING_SALT,
    )

    try:
        response = requests.post(
            settings.APPROVAL_API_URL,
            json={"event": event},
            timeout=5,
            allow_redirects=False,
        )
        response.raise_for_status()

        if response.status_code not in (200, 201):
            raise requests.RequestException(
                "Unexpected approval API response."
            )
    except requests.RequestException:
        logger.exception(
            "Approval API notification failed for article %s.",
            article.pk,
        )
        warnings.append("The internal API notification failed.")

    return warnings
