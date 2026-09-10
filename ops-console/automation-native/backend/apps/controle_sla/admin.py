from django.contrib import admin

from apps.controle_sla import models as m

admin.site.register(m.SlaBreach)
admin.site.register(m.EtapaGap)
