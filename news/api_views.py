"""Token-authenticated article endpoints and signed internal approval events."""

import json

from django.core import signing
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .models import ApprovalLog, Article, User
from .notifications import APPROVAL_SIGNING_SALT, notify_article_approval
from .serializers import ArticleSerializer


@csrf_exempt
@require_POST
def approved_article_event(request):
    """Receive authenticated approval events from our own application.

    This endpoint uses signed events rather than browser session cookies,
    so it does not rely on a browser CSRF token.
    """
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "Invalid JSON."},
            status=400,
        )

    if not isinstance(payload, dict):
        return JsonResponse(
            {"error": "A JSON object is required."},
            status=400,
        )

    event = payload.get("event")

    if not isinstance(event, str):
        return JsonResponse(
            {"error": "A signed event is required."},
            status=403,
        )

    try:
        data = signing.loads(
            event,
            salt=APPROVAL_SIGNING_SALT,
            max_age=60,
        )
    except signing.BadSignature:
        return JsonResponse(
            {"error": "Invalid or expired event."},
            status=403,
        )

    if not isinstance(data, dict):
        return JsonResponse({"error": "Invalid event."}, status=400)

    article_id = data.get("article_id")

    if type(article_id) is not int:
        return JsonResponse(
            {"error": "An integer article ID is required."},
            status=400,
        )

    article = get_object_or_404(
        Article,
        pk=article_id,
        approved=True,
    )

    log, created = ApprovalLog.objects.get_or_create(
        article=article,
    )

    return JsonResponse(
        {
            "article_id": article.pk,
            "logged": True,
            "created": created,
        },
        status=201 if created else 200,
    )


def approved_api_articles(user):
    """Return published articles after checking the user's permission."""
    if not user.has_perm("news.view_article"):
        raise PermissionDenied

    return Article.objects.filter(
        approved=True,
    ).select_related("author", "publisher")


@api_view(["GET", "POST"])
def api_articles(request):
    """List approved articles or accept a journalist's new submission."""
    if request.method == "GET":
        articles = approved_api_articles(request.user)
        return Response(ArticleSerializer(articles, many=True).data)

    if request.user.role != User.Role.JOURNALIST or not request.user.has_perm(
        "news.add_article"
    ):
        raise PermissionDenied("Only journalists can create articles.")

    serializer = ArticleSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)

    # Submitted author and approval values cannot override these.
    serializer.save(
        author=request.user,
        approved=False,
    )

    return Response(
        serializer.data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
def api_subscribed_articles(request):
    """List approved articles matching a reader's subscriptions."""
    if request.user.role != User.Role.READER:
        raise PermissionDenied("Only readers have subscription feeds.")

    articles = (
        approved_api_articles(request.user)
        .filter(
            Q(publisher__in=request.user.subscribed_publishers.all())
            | Q(author__in=request.user.subscribed_journalists.all())
        )
        .distinct()
    )

    # distinct() prevents duplicates when both subscriptions match.
    return Response(ArticleSerializer(articles, many=True).data)


@api_view(["GET", "PUT", "DELETE"])
def api_article_detail(request, pk):
    """Retrieve published content, update an article, or delete it."""
    if request.method == "GET":
        article = get_object_or_404(
            approved_api_articles(request.user),
            pk=pk,
        )
        return Response(ArticleSerializer(article).data)

    # Authors and editors must also be able to manage unpublished work.
    article = get_object_or_404(
        Article.objects.select_related("author", "publisher"),
        pk=pk,
    )

    if request.method == "DELETE":
        check_article_management(request.user, article, "delete")
        article.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    check_article_management(request.user, article, "change")

    serializer = ArticleSerializer(
        article,
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)

    # API updates return the article to review before republication.
    serializer.save(approved=False)

    return Response(serializer.data)


def check_article_management(user, article, action):
    """Require the action permission and editor or author access."""
    if not user.has_perm(f"news.{action}_article"):
        raise PermissionDenied

    is_editor = user.has_perm("news.approve_article")
    is_author = (
        user.role == User.Role.JOURNALIST and article.author_id == user.pk
    )

    if not (is_editor or is_author):
        raise PermissionDenied(
            "You cannot manage another journalist's article."
        )


@api_view(["POST"])
def api_article_approve(request, pk):
    """Allow editors to approve articles and trigger notifications."""
    if not request.user.has_perm("news.approve_article"):
        raise PermissionDenied("Only editors can approve articles.")

    article = get_object_or_404(Article, pk=pk)

    updated = Article.objects.filter(
        pk=article.pk,
        approved=False,
    ).update(
        approved=True,
        updated_at=timezone.now(),
    )

    warnings = []

    if updated:
        article.refresh_from_db()
        warnings = notify_article_approval(article)

    return Response(
        {
            "article": ArticleSerializer(article).data,
            "newly_approved": bool(updated),
            "warnings": warnings,
        }
    )
