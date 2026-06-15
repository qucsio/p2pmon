import io

import xlsxwriter

from apps.orders.models import P2POrder


def export_orders_display(exchange_account) -> io.BytesIO:
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True, "datetime_format": "yyyy-mm-dd hh:mm:ss"})
    worksheet = workbook.add_worksheet("Orders_Display")

    fmt_rub = workbook.add_format({"num_format": "0.00"})
    fmt_usdt = workbook.add_format({"num_format": "0.0000"})
    fmt_price = workbook.add_format({"num_format": "0.00"})
    fmt_fee = workbook.add_format({"num_format": "0.000000"})
    fmt_dt = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})

    headers = [
        "Order ID", "Date", "Side", "Quantity", "Price", "RUB",
        "Fee", "Fee Currency", "Counterparty",
    ]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header)

    orders = P2POrder.objects.filter(
        exchange_account=exchange_account,
        show_in_export=True,
    ).order_by("created_at_moscow")

    for row_idx, order in enumerate(orders, start=1):
        worksheet.write(row_idx, 0, order.bybit_order_id)
        worksheet.write_datetime(row_idx, 1, order.created_at_moscow.replace(tzinfo=None), fmt_dt)
        worksheet.write(row_idx, 2, order.side)
        worksheet.write_number(row_idx, 3, float(order.quantity_net), fmt_usdt)
        worksheet.write_number(row_idx, 4, float(order.price), fmt_price)
        worksheet.write_number(row_idx, 5, float(order.amount_rub), fmt_rub)
        worksheet.write_number(row_idx, 6, float(order.fee_amount), fmt_fee)
        worksheet.write(row_idx, 7, order.fee_currency)
        worksheet.write(row_idx, 8, order.counterparty_name)

    workbook.close()
    output.seek(0)
    return output
