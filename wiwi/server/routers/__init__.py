"""HTTP routers.

One module per admin surface. A router module owns the *HTTP shape* of its
routes (paths, methods, request/response models) and delegates the rules to
``wiwi/server/services/``. The routers are built per app in ``create_app``,
because the service and the auth guard are per-app objects.
"""
