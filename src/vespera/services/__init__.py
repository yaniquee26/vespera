"""The service layer: what Vespera does, without any console or web code.

The command line (`vespera.cli`) and the local web app call these functions. A service never
prints and never exits the process: it returns data or raises a `ServiceError` whose message is
written for the person using Vespera. See docs/adr/0005.
"""
