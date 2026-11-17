"""Named browser routes for reading, authoring and subscriptions."""

from django.urls import path

from . import views

app_name = "news"

urlpatterns = [
    path("publishers/", views.publisher_list, name="publisher-list"),
    path(
        "publishers/create/", views.publisher_create, name="publisher-create"
    ),
    path(
        "publishers/<int:pk>/", views.publisher_detail, name="publisher-detail"
    ),
    path(
        "publishers/<int:pk>/edit/",
        views.publisher_edit,
        name="publisher-edit",
    ),
    path("", views.article_list, name="article-list"),
    path(
        "articles/<int:pk>/",
        views.article_detail,
        name="article-detail",
    ),
    path(
        "articles/<int:pk>/approve/",
        views.approve_article,
        name="article-approve",
    ),
    path(
        "articles/create/",
        views.article_create,
        name="article-create",
    ),
    path(
        "articles/<int:pk>/edit/",
        views.article_edit,
        name="article-edit",
    ),
    path(
        "articles/<int:pk>/delete/",
        views.article_delete,
        name="article-delete",
    ),
    path(
        "newsletters/",
        views.newsletter_list,
        name="newsletter-list",
    ),
    path(
        "newsletters/create/",
        views.newsletter_create,
        name="newsletter-create",
    ),
    path(
        "newsletters/<int:pk>/",
        views.newsletter_detail,
        name="newsletter-detail",
    ),
    path(
        "newsletters/<int:pk>/edit/",
        views.newsletter_edit,
        name="newsletter-edit",
    ),
    path(
        "newsletters/<int:pk>/delete/",
        views.newsletter_delete,
        name="newsletter-delete",
    ),
    path(
        "subscriptions/",
        views.subscription_manage,
        name="subscription-manage",
    ),
]
