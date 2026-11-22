"""Opt-in baseline operand recording, also in spawned Python workers."""

import os

if os.environ.get("RWX_PROFILE_KEYS") == "1":
    from key_trace import enable

    enable()
elif os.environ.get("RWX_PROFILE_VALUES") == "1":
    from value_trace import enable

    enable()
