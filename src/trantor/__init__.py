"""Trantor: XAUUSD-P research and trading system.

Packages: ``strategy`` (approved rules), ``research`` (backtests and
experiments), ``runtime`` (live shadow/paper pipeline), ``execution`` (gate,
paper broker; no live adapter), ``monitoring`` (notifications, logs),
``dashboard`` (read-only UI). Legacy GC/MGC components (``scanners``,
``market_data.gold``) are retained for history. No module sends broker orders.
"""

__version__ = "0.2.0"
