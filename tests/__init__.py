"""Test suite for KeyMux."""

import logging

# The tests run real servers and real sockets, so asyncio's slow-callback
# watchdog fires on ordinary scheduling delays and buries the test result in
# "Executing <Task ...> took 0.3 seconds" noise. The delays are the test
# harness starting servers, not a problem in the code under test.
logging.getLogger("asyncio").setLevel(logging.ERROR)
