from django import forms

from apps.exchange.models import ExchangeAccount


class ExchangeAccountAdminForm(forms.ModelForm):
    api_key_plain = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="API Key (leave blank to keep current)",
    )
    api_secret_plain = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="API Secret (leave blank to keep current)",
    )

    class Meta:
        model = ExchangeAccount
        fields = ("user", "name", "exchange", "is_active")
