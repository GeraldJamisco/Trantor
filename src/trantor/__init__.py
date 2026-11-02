"""Trantor: a COMEX gold futures research and trading-system foundation.

Phases 1 through 3 contain configuration, domain models, safety, the broker
interface, broker-independent market-data contracts, a COMEX GC/MGC session
calendar, replay validation, and research-only gold analysis. They do not scan
live markets, schedule jobs, send notifications, render a dashboard, connect to
a broker, or place orders.
"""

__version__ = "0.1.0"
