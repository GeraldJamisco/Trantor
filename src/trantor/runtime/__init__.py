"""Trantor operational runtime.

This package connects the approved XAUUSD-P strategy to live (or replayed)
market data, persistence, risk state, paper simulation, monitoring and
notifications. It contains no strategy rules of its own: every entry decision
comes from :mod:`trantor.strategy.xauusd_p`.

Execution boundaries:

- ``disabled`` — nothing runs.
- ``research`` — shadow mode: live data, decisions and hypothetical plans; no
  orders of any kind.
- ``paper`` — shadow mode plus the in-process :class:`PaperBroker`; no broker
  order is ever sent.
- ``demo`` / ``live`` — rejected by the execution gate unless the separate
  authorization requirements in :mod:`trantor.execution.gate` are met.
"""
