from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect

from apps.common.helpers import get_active_account
from apps.exports.services.orders_display import export_orders_display


@login_required
def orders_export(request):
    account = get_active_account(request.user)
    if not account:
        return redirect("reports:dashboard")
    output = export_orders_display(account)
    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="Orders_Display.xlsx"'
    return response
