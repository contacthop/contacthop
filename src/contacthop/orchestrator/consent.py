"""SMS consent keywords (TCPA / carrier requirements).

STOP must immediately halt outbound messaging to that number, START resumes,
HELP gets an informational reply. Keywords are detected on every inbound SMS
before anything else; the resulting consent state lives on ChannelIdentity
and is enforced in the gateway backstop.
"""

from __future__ import annotations

from enum import StrEnum

# Carrier-standard keyword sets (case-insensitive, entire message).
OPT_OUT_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE"}
OPT_IN_KEYWORDS = {"START", "UNSTOP", "YES"}
HELP_KEYWORDS = {"HELP", "INFO"}


class ConsentAction(StrEnum):
    OPT_OUT = "opt_out"
    OPT_IN = "opt_in"
    HELP = "help"
    NONE = "none"


def classify(body: str) -> ConsentAction:
    keyword = body.strip().upper()
    if keyword in OPT_OUT_KEYWORDS:
        return ConsentAction.OPT_OUT
    if keyword in OPT_IN_KEYWORDS:
        return ConsentAction.OPT_IN
    if keyword in HELP_KEYWORDS:
        return ConsentAction.HELP
    return ConsentAction.NONE
