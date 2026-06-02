"""FreshDirect automation adapter.

All DOM/Playwright interaction is confined to this package and exposed through the
``FreshDirectAdapter`` Protocol in :mod:`app.freshdirect.base`. The rest of the
application depends on the Protocol and the domain models only — never on the
page structure — so a FreshDirect redesign is contained to this one place.
"""
