"""Browser workflows with session authentication and object ownership checks."""

from django.contrib import messages
from django.contrib.auth import login
from django.db import transaction
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from .forms import (
    ArticleForm,
    NewsletterForm,
    SubscriptionForm,
    RegistrationForm,
    PublisherForm,
)
from .models import Article, Newsletter, Publisher, User
from .roles import configure_role_groups
from .notifications import notify_article_approval


def visible_articles(user):
    """Return the articles the user has permission to read.

    After the view permission check, superusers and users with approval
    permission can see all articles. Journalists can see approved articles
    and their own drafts. Other users can see approved articles only.

    :param user: The account requesting article access.
    :return: A queryset with authors and publishers selected.
    :raises PermissionDenied: If the user lacks article view permission.
    """
    if not user.has_perm("news.view_article"):
        raise PermissionDenied

    articles = Article.objects.select_related("author", "publisher")

    # Editors review all submissions. Superusers retain full access.
    if user.is_superuser or user.has_perm("news.approve_article"):
        return articles

    # Journalists can read published articles and their own drafts.
    if user.role == User.Role.JOURNALIST:
        return articles.filter(Q(approved=True) | Q(author=user))

    # Readers only see content that has been approved.
    return articles.filter(approved=True)


@login_required
def article_list(request):
    """Display articles available to the signed-in user."""
    articles = visible_articles(request.user)

    return render(
        request,
        "news/article_list.html",
        {"articles": articles},
    )


@login_required
def article_detail(request, pk):
    """Display an article only if the user can access it."""
    article = get_object_or_404(
        visible_articles(request.user),
        pk=pk,
    )

    return render(
        request,
        "news/article_detail.html",
        {"article": article},
    )


@login_required
@permission_required("news.approve_article", raise_exception=True)
@require_POST
def approve_article(request, pk):
    """Approve an article through a POST and redirect to its detail page.

    Send notifications only when the article changes from unapproved to
    approved. Show any delivery warnings to the editor without undoing
    the approval.

    :param request: The browser request.
    :param pk: The article's primary key.
    :return: A redirect to the article detail page.
    """
    article = get_object_or_404(Article, pk=pk)

    # A conditional update prevents repeated approval clicks from
    # triggering another round of emails and API notifications.
    updated = Article.objects.filter(
        pk=article.pk,
        approved=False,
    ).update(
        approved=True,
        updated_at=timezone.now(),
    )

    if not updated:
        messages.info(request, "This article is already approved.")
        return redirect("news:article-detail", pk=article.pk)

    article.refresh_from_db()
    warnings = notify_article_approval(article)

    messages.success(request, "The article has been approved.")

    for warning in warnings:
        messages.warning(request, warning)

    return redirect("news:article-detail", pk=article.pk)


@login_required
@permission_required("news.add_article", raise_exception=True)
def article_create(request):
    """Allow journalists to submit a new article for approval."""
    if request.user.role != User.Role.JOURNALIST:
        raise PermissionDenied

    form = ArticleForm(
        request.POST if request.method == "POST" else None,
        user=request.user,
    )

    if request.method == "POST" and form.is_valid():
        article = form.save(commit=False)
        article.approved = False
        article.save()

        messages.success(
            request,
            "Your article has been submitted for editor approval.",
        )
        return redirect("news:article-detail", pk=article.pk)

    return render(
        request,
        "news/article_form.html",
        {"form": form, "heading": "Create article"},
    )


@login_required
@permission_required("news.change_article", raise_exception=True)
def article_edit(request, pk):
    """Display the edit form or process an article update.

    The caller must have approval permission or be the journalist who
    wrote the article. A valid submission with changes clears approval
    so the article can be reviewed again. An unchanged submission keeps
    the existing approval status.

    :param request: The browser request.
    :param pk: The article's primary key.
    :return: The edit form or a redirect after a valid submission.
    """
    article = get_object_or_404(Article, pk=pk)

    is_editor = request.user.has_perm("news.approve_article")
    is_author = (
        request.user.role == User.Role.JOURNALIST
        and article.author_id == request.user.pk
    )

    if not (is_editor or is_author):
        raise PermissionDenied

    form = ArticleForm(
        request.POST if request.method == "POST" else None,
        instance=article,
        user=request.user,
    )

    if request.method == "POST" and form.is_valid():
        if form.has_changed():
            article = form.save(commit=False)
            article.approved = False
            article.save()

            messages.success(
                request,
                "Changes saved. The article now awaits editor approval.",
            )
        else:
            messages.info(request, "No changes were made.")

        return redirect("news:article-detail", pk=article.pk)

    return render(
        request,
        "news/article_form.html",
        {"form": form, "heading": "Edit article"},
    )


@login_required
@permission_required("news.delete_article", raise_exception=True)
def article_delete(request, pk):
    """Let editors or journalist authors confirm and delete an article."""
    article = get_object_or_404(Article, pk=pk)

    is_editor = request.user.has_perm("news.approve_article")
    is_author = (
        request.user.role == User.Role.JOURNALIST
        and article.author_id == request.user.pk
    )

    if not (is_editor or is_author):
        raise PermissionDenied

    if request.method == "POST":
        article.delete()
        messages.success(request, "The article has been deleted.")
        return redirect("news:article-list")

    return render(
        request,
        "news/article_confirm_delete.html",
        {"article": article},
    )


@login_required
@permission_required("news.view_newsletter", raise_exception=True)
def newsletter_list(request):
    """Display newsletters available to signed-in users."""
    newsletters = Newsletter.objects.select_related(
        "author",
        "publisher",
    )

    return render(
        request,
        "news/newsletter_list.html",
        {"newsletters": newsletters},
    )


@login_required
@permission_required("news.view_newsletter", raise_exception=True)
def newsletter_detail(request, pk):
    """Display a newsletter without exposing unpublished articles."""
    newsletter = get_object_or_404(
        Newsletter.objects.select_related("author", "publisher"),
        pk=pk,
    )

    # An article might have returned to draft after being edited.
    # Filter again when displaying the newsletter to protect that content.
    articles = newsletter.articles.filter(
        approved=True,
    ).select_related("author", "publisher")

    return render(
        request,
        "news/newsletter_detail.html",
        {"newsletter": newsletter, "articles": articles},
    )


@login_required
@permission_required("news.add_newsletter", raise_exception=True)
def newsletter_create(request):
    """Allow journalists and editors to curate approved articles."""
    if request.user.role not in (User.Role.JOURNALIST, User.Role.EDITOR):
        raise PermissionDenied

    form = NewsletterForm(
        request.POST if request.method == "POST" else None,
        user=request.user,
    )

    if request.method == "POST" and form.is_valid():
        newsletter = form.save()
        messages.success(request, "Your newsletter has been created.")

        return redirect(
            "news:newsletter-detail",
            pk=newsletter.pk,
        )

    return render(
        request,
        "news/newsletter_form.html",
        {"form": form},
    )


@login_required
@permission_required("news.change_newsletter", raise_exception=True)
def newsletter_edit(request, pk):
    """Allow editors or journalist authors to edit a newsletter."""
    newsletter = get_object_or_404(Newsletter, pk=pk)

    is_editor = request.user.has_perm("news.approve_article")
    is_author = (
        request.user.role == User.Role.JOURNALIST
        and newsletter.author_id == request.user.pk
    )

    if not (is_editor or is_author):
        raise PermissionDenied

    form = NewsletterForm(
        request.POST if request.method == "POST" else None,
        instance=newsletter,
        user=request.user,
    )

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Newsletter changes saved.")

        return redirect(
            "news:newsletter-detail",
            pk=newsletter.pk,
        )

    return render(
        request,
        "news/newsletter_form.html",
        {"form": form, "heading": "Edit newsletter"},
    )


@login_required
@permission_required("news.delete_newsletter", raise_exception=True)
def newsletter_delete(request, pk):
    """Allow authorised users to confirm and delete a newsletter."""
    newsletter = get_object_or_404(Newsletter, pk=pk)

    is_editor = request.user.has_perm("news.approve_article")
    is_author = (
        request.user.role == User.Role.JOURNALIST
        and newsletter.author_id == request.user.pk
    )

    if not (is_editor or is_author):
        raise PermissionDenied

    if request.method == "POST":
        # Deleting the collection preserves its individual articles.
        newsletter.delete()
        messages.success(request, "The newsletter has been deleted.")
        return redirect("news:newsletter-list")

    return render(
        request,
        "news/newsletter_confirm_delete.html",
        {"newsletter": newsletter},
    )


@login_required
def subscription_manage(request):
    """Allow readers to update subscriptions on their own account."""
    if request.user.role != User.Role.READER:
        raise PermissionDenied

    # Bind to the signed-in account, never to a submitted user ID.
    form = SubscriptionForm(
        request.POST if request.method == "POST" else None,
        instance=request.user,
    )

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your subscriptions have been updated.")
        return redirect("news:subscription-manage")

    return render(
        request,
        "news/subscription_form.html",
        {"form": form},
    )


@require_http_methods(["GET", "POST"])
def register(request):
    """Register a selected role and start its ordinary authenticated session.

    Configure role permissions before saving. The user-save signal assigns
    the matching group, so a first-time installation needs no admin-created
    demo accounts before its browser journeys can be exercised.
    """
    if request.user.is_authenticated:
        return redirect("news:article-list")
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            configure_role_groups()
            user = form.save()
        login(request, user)
        messages.success(
            request, "Your account is ready. Welcome to Newsroom."
        )
        return redirect("news:article-list")
    return render(request, "registration/register.html", {"form": form})


@login_required
def publisher_list(request):
    """List publications and identify those managed by the current editor."""
    managed_ids = set(
        request.user.publishers_as_editor.values_list("pk", flat=True)
    )
    return render(
        request,
        "news/publisher_list.html",
        {
            "publishers": Publisher.objects.all(),
            "managed_ids": managed_ids,
        },
    )


@login_required
def publisher_detail(request, pk):
    """Show a publication's newsroom and only the stories visible to this user."""
    publisher = get_object_or_404(
        Publisher.objects.prefetch_related("editors", "journalists"), pk=pk
    )
    can_manage = (
        request.user.role == User.Role.EDITOR
        and publisher.editors.filter(pk=request.user.pk).exists()
    )
    return render(
        request,
        "news/publisher_detail.html",
        {
            "publisher": publisher,
            "articles": visible_articles(request.user).filter(
                publisher=publisher
            ),
            "can_manage": can_manage,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def publisher_create(request):
    """Create a publication and give its creating editor management access."""
    if request.user.role != User.Role.EDITOR:
        raise PermissionDenied
    form = PublisherForm(
        request.POST if request.method == "POST" else None, user=request.user
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            publisher = form.save()
        messages.success(
            request, "Publication created. You can manage its newsroom here."
        )
        return redirect("news:publisher-detail", pk=publisher.pk)
    return render(
        request,
        "news/publisher_form.html",
        {
            "form": form,
            "heading": "Create publisher",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def publisher_edit(request, pk):
    """Permit affiliated editors to change publication details and members.

    The membership check runs on the server for GET and POST alike; hiding
    a link alone would not protect another editor's publication.
    """
    publisher = get_object_or_404(Publisher, pk=pk)
    if (
        request.user.role != User.Role.EDITOR
        or not publisher.editors.filter(pk=request.user.pk).exists()
    ):
        raise PermissionDenied
    form = PublisherForm(
        request.POST if request.method == "POST" else None,
        instance=publisher,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        messages.success(request, "Publication and newsroom members updated.")
        return redirect("news:publisher-detail", pk=publisher.pk)
    return render(
        request,
        "news/publisher_form.html",
        {
            "form": form,
            "heading": "Manage publisher",
        },
    )
