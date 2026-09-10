# -*- coding: utf-8 -*-
from apps.access import registry as R
from apps.access.permissions import HasPortalPermission


class CanViewCaseManager(HasPortalPermission):
    permission_code = R.INDICADORES_CASE_MANAGER_VIEW


class CanSyncCaseManager(HasPortalPermission):
    permission_code = R.INDICADORES_CASE_MANAGER_SYNC
