"""REST article routes; token login and internal events live in config.urls."""

from django.urls import path

from . import api_views

app_name = "news-api"

urlpatterns = [
    path(
        "articles/",
        api_views.api_articles,
        name="article-list",
    ),
    path(
        "articles/subscribed/",
        api_views.api_subscribed_articles,
        name="article-subscribed",
    ),
    path(
        "articles/<int:pk>/",
        api_views.api_article_detail,
        name="article-detail",
    ),
    path(
        "articles/<int:pk>/approve/",
        api_views.api_article_approve,
        name="article-approve",
    ),
]
