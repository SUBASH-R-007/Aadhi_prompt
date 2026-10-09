"""HTTP security: headers/CSP, CSRF, body limits, client IP, rate limits, upload validation.

Import the submodules directly (``aadhi.security.headers`` ...); this package does not import them
eagerly so that e.g. ``aadhi.security.uploads`` can be used without FastAPI request machinery.
"""
