from apps.exchange.models import ExchangeAccount


def get_active_account(user):
    return ExchangeAccount.objects.filter(user=user, is_active=True).first()
