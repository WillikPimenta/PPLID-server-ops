from rest_framework.throttling import UserRateThrottle


class ReplicacaoD1DashboardThrottle(UserRateThrottle):
    scope = "replicacao_d1_dashboard"
