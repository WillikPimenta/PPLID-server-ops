from django.contrib import admin

from .models import News, NewsAcknowledgment, NewsImage, NewsLike, PortalFeedback


class NewsImageInline(admin.TabularInline):
    model = NewsImage
    extra = 0
    fields = ("image", "sort_order", "created_at")
    readonly_fields = ("created_at",)


@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "author", "published_at", "is_critical", "active")
    list_filter = ("category", "active", "is_critical", "published_at")
    search_fields = ("title", "author", "summary", "content")
    ordering = ("-published_at",)
    inlines = [NewsImageInline]


@admin.register(NewsImage)
class NewsImageAdmin(admin.ModelAdmin):
    list_display = ("news", "sort_order", "created_at")
    list_filter = ("created_at",)
    search_fields = ("news__title",)
    ordering = ("news", "sort_order")


@admin.register(NewsAcknowledgment)
class NewsAcknowledgmentAdmin(admin.ModelAdmin):
    list_display = ("news", "user", "acknowledged_at")
    list_filter = ("acknowledged_at",)
    search_fields = ("news__title", "user__username", "user__email")
    readonly_fields = ("acknowledged_at",)
    ordering = ("-acknowledged_at",)
    raw_id_fields = ("news", "user")


@admin.register(NewsLike)
class NewsLikeAdmin(admin.ModelAdmin):
    list_display = ("news", "user", "created_at")
    list_filter = ("created_at",)
    search_fields = ("news__title", "user__username", "user__email")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)
    raw_id_fields = ("news", "user")


@admin.register(PortalFeedback)
class PortalFeedbackAdmin(admin.ModelAdmin):
    list_display = ("protocol", "kind", "subject", "status", "created_by", "created_at")
    list_filter = ("kind", "status", "allow_contact", "created_at")
    search_fields = ("protocol", "subject", "description", "created_by__username")
    readonly_fields = ("protocol", "created_at", "updated_at", "handled_at")
    ordering = ("-created_at",)
    raw_id_fields = ("created_by", "handled_by")
