"""Persist successful life_order into mem0; failures must not call this."""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable


def diary_line(
    *,
    service: str,
    amount_yuan: float,
    order_no: str,
    when: dt.datetime | None = None,
    extra: str = "",
) -> str:
    when = when or dt.datetime.now()
    ts = when.strftime("%Y-%m-%d %H:%M")
    base = f"{ts} 帮他下了{service}单，¥{float(amount_yuan):g}，单号{order_no}"
    if extra:
        base = f"{base}，{extra}"
    return base


def record_success(
    contact_id: str,
    *,
    service: str,
    amount_yuan: float,
    order_no: str,
    when: dt.datetime | None = None,
    extra: str = "",
    mem_add: Callable[..., Any] | None = None,
) -> Any:
    """mem.add kind=life_order with time / amount / order number + diary line.

    Real successes may use the real mem0 sink. Dry runs may only use an explicitly
    injected sink. Failures must not invoke this.
    """
    when = when or dt.datetime.now()
    line = diary_line(
        service=service,
        amount_yuan=amount_yuan,
        order_no=order_no,
        when=when,
        extra=extra,
    )
    adder = mem_add
    if adder is None:
        import mem
        adder = mem.add
    return adder(
        str(contact_id),
        line,
        importance=7,
        kind="life_order",
        source="life_order",
        ts=when.strftime("%Y-%m-%d %H:%M:%S"),
        evidence={"service": service, "amount_yuan": float(amount_yuan), "order_no": str(order_no)},
    )
