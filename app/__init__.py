"""FreshDirect Weekly Planner — AI grocery agent."""

import logging

__version__ = "0.1.0"

# Patch Python's ssl module to trust the system certificate store (Windows cert
# store on this machine), which includes the corporate proxy CA.  This must run
# before any outbound TLS connection is made so that httpx / the Anthropic SDK
# can reach external APIs through the corporate MITM proxy.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    # truststore not installed; SSL falls back to certifi (fine off the proxy).
    logging.getLogger(__name__).debug("truststore unavailable; using certifi trust store")
